#!/usr/bin/env bash
# gate-hook.sh — PreToolUse hook that moves gate.sh from convention to enforcement.
#
# `gate.sh` was always the one deterministic step in the chain, but nothing made
# anybody run it. A router that forgets, an agent that decides the evidence looks
# fine, a human in a hurry — all three open a PR the gate would have blocked. This
# hook runs the gate at the tool boundary instead, so the PR cannot be opened
# without it.
#
# It engages on exactly three shapes, and it treats them differently on purpose.
#
#   pr-open.sh    is this plugin's own script, so every invocation of it is inside
#                 the workflow by definition. If the artifacts dir cannot be
#                 resolved, the call is DENIED and the reason names the step that
#                 failed. There is no such thing as an unrelated pr-open.sh.
#
#   release-pr.sh is also ours, and is treated the same way: denied when the
#                 release directory cannot be resolved, gated by
#                 `gate.sh --for release` when it can. It matters more than the
#                 other two, not less — the last element of a branch chain is
#                 production. It calls `gh pr create` inside itself rather than
#                 emitting one, so the shape below never sees that call and this
#                 shape is the only thing standing in front of it. Its release
#                 directory is keyed by the branch pair rather than by a task id,
#                 because a promotion belongs to no single task.
#
#   gh pr create  is an ordinary command that any repository on this machine may
#                 reasonably run. It is gated only when the hook can attribute it
#                 to a task: the branch of the worktree carries a leading task id
#                 and exactly one artifacts dir matches that id. When it cannot —
#                 no task id in the branch name, no such directory, no worktree,
#                 or not a git repository at all — the call is ALLOWED in silence,
#                 because this plugin must not reach outside its own workflow.
#
# A shape is matched in command position, never as a substring of the command
# text. Heredoc bodies are dropped first, because the body of a heredoc is data
# rather than commands: a commit message that happens to name pr-open.sh is a
# commit, not a PR. What is left is split into commands on the shell operators
# `;`, `&&`, `||`, `|`, `&` and the newline, and each command is judged by its
# first token alone, with a leading `env`, `bash`, `sh`, `command`, `sudo` or
# `VAR=value` prefix stripped so that a wrapped call still reads as the call it
# is. A name that appears later in a command is an argument, and an argument is
# not an invocation. `grep -rn pr-open.sh`, `cat hooks/gate-hook.sh` and
# `git commit -m "docs: explain pr-open.sh"` are therefore ordinary commands that
# merely mention a name, and this hook says nothing about any of them.
#
# One resolution failure denies in both task-keyed shapes: a task id that matches
# more than one artifacts dir. An ambiguous match is a real inconsistency inside the
# workflow rather than a sign of an unrelated repo, so it is refused by name.
#
# The trade-off this accepts, deliberately, so that nobody "fixes" it later: a
# task branch named without its task id can now bypass the gate through a
# hand-run `gh pr create`. That is the price of the narrowing, and it is worth
# paying. The alternative denies ordinary PRs in unrelated repositories on the
# user's own machine, which is a larger harm than the one it prevents. A PR
# opened that way is also untracked by `odoo-dev:odoo-release`: the manifest
# files it under `unresolved` and its task never learns that it shipped.
#
# Input: the PreToolUse payload as JSON on stdin.
# Output: nothing at all to allow; the deny envelope on stdout to refuse.
# Exit codes: 0 always (allow and deny both ride on exit 0 with JSON) | 2 only if
#             node itself is unavailable for a pr-open.sh or release-pr.sh call.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$HERE/.." && pwd)}"
GATE="$ROOT/scripts/gate.sh"

payload="$(cat)"

# deny <reason> — the PreToolUse deny envelope, exit 0.
deny() {
  local json
  if json="$(REASON="$1" node -e '
process.stdout.write(JSON.stringify({
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: "deny",
    permissionDecisionReason: process.env.REASON,
  },
}));
' 2>/dev/null)"; then
    printf '%s\n' "$json"
    exit 0
  fi
  echo "gate-hook.sh: $1" >&2
  exit 2
}

# Which of the two shapes is this, and where is the worktree? Parsing lives in node
# because the command is a JSON string and the quoting rules are its own.
if ! fields="$(printf '%s' "$payload" | node -e '
const SQ = "\x27";
const unquote = (t) => t.replace(/^["\x27]+/, "").replace(/["\x27]+$/, "");
const base = (t) => { const u = unquote(t); const i = u.lastIndexOf("/"); return i < 0 ? u : u.slice(i + 1); };

// heredocsOn(line) — every heredoc the line opens, in the order their bodies
// follow. The scan tracks quoting, so a << inside a quoted argument is not taken
// for a redirection, and it steps over <<< because a here-string has no body.
const heredocsOn = (line) => {
  const found = [];
  let quote = null;
  for (let i = 0; i < line.length; i += 1) {
    const c = line[i];
    if (quote) {
      if (c === "\\" && quote === "\"") { i += 1; continue; }
      if (c === quote) quote = null;
      continue;
    }
    if (c === "\\") { i += 1; continue; }
    if (c === SQ || c === "\"") { quote = c; continue; }
    if (c !== "<" || line[i + 1] !== "<") continue;
    if (line[i + 2] === "<") { i += 2; continue; }
    let j = i + 2;
    let dash = false;
    if (line[j] === "-") { dash = true; j += 1; }
    while (line[j] === " " || line[j] === "\t") j += 1;
    let marker = "";
    if (line[j] === SQ || line[j] === "\"") {
      const q = line[j];
      j += 1;
      while (j < line.length && line[j] !== q) { marker += line[j]; j += 1; }
      j += 1;
    } else {
      if (line[j] === "\\") j += 1;
      while (j < line.length && /[A-Za-z0-9_.-]/.test(line[j])) { marker += line[j]; j += 1; }
    }
    if (marker) found.push({ marker: marker, dash: dash });
    i = j - 1;
  }
  return found;
};

// stripHeredocs(text) — the text with every heredoc body removed. A body is data,
// not commands, so nothing inside one can name a command this hook gates.
const stripHeredocs = (text) => {
  const lines = text.split("\n");
  const kept = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    i += 1;
    kept.push(line);
    for (const doc of heredocsOn(line)) {
      while (i < lines.length) {
        const body = lines[i];
        i += 1;
        const cand = (doc.dash ? body.replace(/^\t+/, "") : body).replace(/\s+$/, "");
        if (cand === doc.marker) break;
      }
    }
  }
  return kept.join("\n");
};

// splitSegments(text) — the text cut into commands on the shell operators, with
// each token unquoted. Quoting is tracked so that `echo "a; b"` stays one command
// and the words inside a quoted string never reach command position.
const splitSegments = (text) => {
  const segs = [];
  let seg = [];
  let tok = null;
  const endTok = () => { if (tok !== null) { seg.push(tok); tok = null; } };
  const endSeg = () => { endTok(); if (seg.length) segs.push(seg); seg = []; };
  let i = 0;
  while (i < text.length) {
    const c = text[i];
    if (c === SQ) {
      if (tok === null) tok = "";
      i += 1;
      while (i < text.length && text[i] !== SQ) { tok += text[i]; i += 1; }
      i += 1;
      continue;
    }
    if (c === "\"") {
      if (tok === null) tok = "";
      i += 1;
      while (i < text.length && text[i] !== "\"") {
        if (text[i] === "\\" && i + 1 < text.length) { tok += text[i + 1]; i += 2; continue; }
        tok += text[i];
        i += 1;
      }
      i += 1;
      continue;
    }
    if (c === "\\") {
      if (tok === null) tok = "";
      if (i + 1 < text.length) tok += text[i + 1];
      i += 2;
      continue;
    }
    if (c === " " || c === "\t" || c === "\r") { endTok(); i += 1; continue; }
    if (c === "\n" || c === ";" || c === "(" || c === ")") { endSeg(); i += 1; continue; }
    if (c === "&" || c === "|") { endSeg(); i += (text[i + 1] === c ? 2 : 1); continue; }
    if (tok === null) tok = "";
    tok += c;
    i += 1;
  }
  endSeg();
  return segs;
};

const WRAPPERS = ["env", "bash", "sh", "command", "sudo"];
const ASSIGN = /^[A-Za-z_][A-Za-z0-9_]*=/;

// commandsIn(text, depth) — one token list per command position in the text, the
// first token of each being the program that would run. A `VAR=value` prefix and
// an env/bash/sh/command/sudo wrapper are stripped, and the string handed to
// `bash -c` is parsed as the commands it holds rather than as an argument.
const commandsIn = (text, depth) => {
  const out = [];
  for (const seg of splitSegments(stripHeredocs(text))) {
    let toks = seg;
    let inner = null;
    for (let guard = 0; guard < 16 && toks.length; guard += 1) {
      if (ASSIGN.test(toks[0])) { toks = toks.slice(1); continue; }
      const head = base(toks[0]);
      if (WRAPPERS.indexOf(head) < 0) break;
      let rest = toks.slice(1);
      while (rest.length && rest[0].startsWith("-")) {
        if ((head === "bash" || head === "sh") && rest[0] === "-c" && rest.length > 1) {
          inner = rest[1];
          rest = [];
          break;
        }
        rest = rest.slice(1);
      }
      if (inner !== null) break;
      toks = rest;
    }
    if (inner !== null) {
      if (depth < 3) out.push.apply(out, commandsIn(inner, depth + 1));
      continue;
    }
    if (toks.length) out.push(toks);
  }
  return out;
};

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (d) => { raw += d; });
process.stdin.on("end", () => {
  let p = null;
  try { p = JSON.parse(raw); } catch { p = null; }
  const clean = (s) => (s || "").replace(/[\r\n]+/g, " ");
  const emit = (mode, wt, note) =>
    process.stdout.write(mode + "\n" + clean(wt) + "\n" + clean(note) + "\n");

  // A payload we cannot read names no PR command either, so there is nothing to
  // gate. The allowlist hook is the one that fails closed on an unreadable payload.
  if (!p || typeof p !== "object" || Array.isArray(p)) return emit("none", "", "");
  if (p.tool_name !== "Bash") return emit("none", "", "");
  const cmd = p.tool_input && typeof p.tool_input.command === "string" ? p.tool_input.command : "";
  const cwd = typeof p.cwd === "string" ? p.cwd : "";
  const cmds = commandsIn(cmd, 0);

  // Shape one: pr-open.sh, which is ours. Anything unresolvable about the call is
  // a broken call inside the workflow, so it is denied rather than waved through.
  // Only a command position counts: a segment whose FIRST token names the script.
  const prOpen = cmds.filter((c) => c[0].indexOf("pr-open.sh") >= 0);
  if (prOpen.length > 1) {
    return emit("pr-open-unresolved", "", "the command names pr-open.sh " + prOpen.length +
      " times, so which worktree is being pushed is not decidable");
  }
  if (prOpen.length === 1) {
    const call = prOpen[0];
    // The script run in command position but named some other way (through a
    // variable, say) is still a PR being opened through tooling of ours, and the
    // worktree is still unresolvable. Deny.
    if (base(call[0]) !== "pr-open.sh") {
      return emit("pr-open-unresolved", "",
        "the command mentions pr-open.sh but not as a resolvable token, so the worktree is unknown");
    }
    const next = call.length > 1 ? call[1] : "";
    if (!next || next.startsWith("-")) {
      return emit("pr-open-unresolved", "",
        "pr-open.sh was called without its first positional argument, so the worktree is unknown");
    }
    return emit("pr-open", next, "");
  }

  // Shape two: release-pr.sh, also ours, and unresolvable the same way means the
  // same thing — a broken call inside the workflow, denied rather than waved
  // through. What differs is where the evidence lives: this script takes no
  // worktree, so the artifacts dir is built from its <from> and <to> positionals
  // as <releases>/<from>-to-<to>, which is the key `commands/pr.md` writes.
  const relPr = cmds.filter((c) => c[0].indexOf("release-pr.sh") >= 0);
  if (relPr.length > 1) {
    return emit("release-pr-unresolved", "", "the command names release-pr.sh " + relPr.length +
      " times, so which release is being opened is not decidable");
  }
  if (relPr.length === 1) {
    const call = relPr[0];
    if (base(call[0]) !== "release-pr.sh") {
      return emit("release-pr-unresolved", "",
        "the command mentions release-pr.sh but not as a resolvable token, so the release directory is unknown");
    }
    // <owner/repo> <from> <to> are positional and come first: the script shifts
    // all three before it looks at a single flag.
    const pos = call.slice(1, 4);
    if (pos.length < 3 || pos.some((a) => !a || a.startsWith("-"))) {
      return emit("release-pr-unresolved", "",
        "release-pr.sh was called without its <owner/repo> <from> <to> positional arguments, so the release directory is unknown");
    }
    const from = pos[1], to = pos[2];
    // The pair becomes a directory name, so it is held to plain branch names for
    // the same reason the command that creates the directory is.
    const PLAIN = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
    if (!PLAIN.test(from) || !PLAIN.test(to) || from.indexOf("..") >= 0 || to.indexOf("..") >= 0) {
      return emit("release-pr-unresolved", "",
        "\"" + from + "\" and \"" + to + "\" are not both plain branch names, so no release directory key can be built from them");
    }
    return emit("release-pr", from + "-to-" + to, "");
  }

  // Shape three: a bare `gh pr create`, again only in command position, and only
  // when `pr` and `create` are its first two non-flag arguments. The payload cwd
  // is the only worktree such a call carries, and without one there is nothing to
  // attribute the PR to, so this hook has no business with it.
  for (const call of cmds) {
    if (base(call[0]) !== "gh") continue;
    const pos = [];
    for (let i = 1; i < call.length && pos.length < 2; i += 1) {
      const a = call[i];
      // -R and --repo take a value, and that value is not the subcommand.
      if (a.startsWith("-")) { if (a === "-R" || a === "--repo") i += 1; continue; }
      pos.push(a);
    }
    if (pos[0] === "pr" && pos[1] === "create") {
      if (!cwd) return emit("gh-unattributable", "", "");
      return emit("gh", cwd, "");
    }
  }
  return emit("none", "", "");
});
' 2>/dev/null)"; then
  # Without node the hook cannot parse the payload, build a deny envelope, or run
  # the gate. A pr-open.sh or release-pr.sh call is inside the workflow and is
  # refused loudly. A bare `gh pr create` cannot be attributed to a task even in
  # principle here, so it follows the rule every unattributable PR follows and
  # passes through. These tests stay substring tests on purpose: with node gone
  # there is nothing left to parse the command with, and a loud refusal is the
  # whole point of the branch.
  case "$payload" in
    *pr-open.sh*|*release-pr.sh*)
      echo "gate-hook.sh: node is unavailable, so gate.sh cannot be run before this PR. Denying rather than opening an ungated PR." >&2
      exit 2
      ;;
  esac
  exit 0
fi

mode="$(printf '%s\n' "$fields" | sed -n 1p)"
worktree="$(printf '%s\n' "$fields" | sed -n 2p)"
note="$(printf '%s\n' "$fields" | sed -n 3p)"

[ "$mode" = "none" ] && exit 0
# A `gh pr create` whose payload carries no worktree cannot be tied to a task, and
# an unattributable PR is not this plugin's business. See the header comment.
[ "$mode" = "gh-unattributable" ] && exit 0

WHY="A PR that cannot be traced back to its evidence does not open. Run gate.sh yourself against the task artifacts dir and fix what it names, or call pr-open.sh with the worktree as its first argument."
RELEASE_WHY="A promotion that cannot be traced back to its manifest does not open. Write 60-release.json into the release directory through artifact.sh, run gate.sh --for release against it, and fix what it names."

if [ "$mode" = "pr-open-unresolved" ]; then
  deny "The odoo-dev gate hook cannot resolve the artifacts dir for this PR: $note. $WHY"
fi

if [ "$mode" = "release-pr-unresolved" ]; then
  deny "The odoo-dev gate hook cannot resolve the release directory for this promotion: $note. $RELEASE_WHY"
fi

# unattributable <reason> — the fork between the two shapes, in one place. For
# pr-open.sh, failing to resolve the artifacts dir means the workflow is broken,
# and the call is denied. For a bare `gh pr create` it means the PR belongs to
# work this plugin does not manage, and the call passes through untouched.
unattributable() {
  if [ "$mode" = "pr-open" ]; then
    deny "The odoo-dev gate hook cannot resolve the artifacts dir for this PR: $1 $WHY"
  fi
  exit 0
}

# The release shape resolves nothing from a worktree: it carries the release
# directory key in the same slot, and the whole task-id derivation below is
# skipped. `gate.sh --for release` is what judges it.
if [ "$mode" = "release-pr" ]; then
  RELEASES="${ODOO_DEV_STATE_DIR:-$HOME/.local/share/odoo-dev}/releases"
  ARTIFACTS="$RELEASES/$worktree"
  GATE_FOR=release
  if [ ! -d "$ARTIFACTS" ]; then
    deny "The odoo-dev gate hook cannot resolve the release directory for this promotion: no directory at $ARTIFACTS. $RELEASE_WHY"
  fi
else
  [ -d "$worktree" ] || unattributable "the worktree path \"$worktree\" is not a directory."

  branch="$(git -C "$worktree" rev-parse --abbrev-ref HEAD 2>/dev/null)"
  # "HEAD" is what rev-parse prints for a detached or unborn HEAD, and neither names
  # a task branch.
  if [ -z "$branch" ] || [ "$branch" = "HEAD" ]; then
    unattributable "\"$worktree\" is not a git worktree with a named branch checked out."
  fi

  # Branches here look like 30412-stockflow-sync, so the task id is the leading run of
  # digits. A branch that does not start with one cannot be matched to a task dir.
  task_id="$(printf '%s' "$branch" | sed -n 's/^\([0-9][0-9]*\).*/\1/p')"
  [ -n "$task_id" ] || unattributable "branch \"$branch\" carries no leading task id, so there is no \$ODOO_DEV_STATE_DIR/tasks/<task_id> to check. Branch names in this workflow look like 30412-stockflow-sync."

  TASKS="${ODOO_DEV_STATE_DIR:-$HOME/.local/share/odoo-dev}/tasks"

  if [ -d "$TASKS/$task_id" ]; then
    ARTIFACTS="$TASKS/$task_id"
  else
    # An exact match is unambiguous by construction. Without one, a single prefix
    # match is accepted and anything else is refused by name.
    cands=()
    for c in "$TASKS/$task_id"*; do [ -d "$c" ] && cands+=("$c"); done
    if [ "${#cands[@]}" -eq 1 ]; then
      ARTIFACTS="${cands[0]}"
    elif [ "${#cands[@]}" -eq 0 ]; then
      unattributable "no directory at $TASKS/$task_id (branch \"$branch\"). No artifacts means no evidence, and no evidence means no PR."
    else
      # Two dirs for one task id is the one resolution failure that denies whichever
      # shape asked. The task id resolved, so the PR is inside this workflow, and a
      # workflow that has filed one task's evidence in two places is broken in a way
      # that guessing would only hide.
      deny "The odoo-dev gate hook cannot resolve the artifacts dir for this PR: task id $task_id from branch \"$branch\" matches ${#cands[@]} directories under $TASKS (${cands[*]}), so which one holds the evidence is not decidable. Merge or remove the duplicates so that one task id names one artifacts dir, then retry. $WHY"
    fi
  fi

  GATE_FOR=pr
fi

errf="$(mktemp)"
gout="$(bash "$GATE" "$ARTIFACTS" --for "$GATE_FOR" 2>"$errf")"; rc=$?
gerr="$(cat "$errf")"; rm -f "$errf"

[ "$rc" -eq 0 ] && exit 0

if [ "$rc" -ne 1 ]; then
  if [ "$GATE_FOR" = release ]; then
    deny "The odoo-dev gate hook could not run gate.sh against $ARTIFACTS (exit $rc: ${gerr:-no output}), so it cannot confirm the manifest. $RELEASE_WHY"
  fi
  deny "The odoo-dev gate hook could not run gate.sh against $ARTIFACTS (exit $rc: ${gerr:-no output}), so it cannot confirm the evidence. $WHY"
fi

summary="$(printf '%s' "$gout" | node -e '
let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (d) => { raw += d; });
process.stdin.on("end", () => {
  const lines = raw.trim().split("\n").filter(Boolean);
  let j = null;
  try { j = JSON.parse(lines[lines.length - 1]); } catch { j = null; }
  if (!j || !Array.isArray(j.blockers) || !j.blockers.length) {
    process.stdout.write("gate.sh reported a failure but printed no parseable blocker list");
    return;
  }
  process.stdout.write(j.blockers.map((b) => b.blocker + " (" + b.detail + ")").join("; "));
});
' 2>/dev/null)"
[ -n "$summary" ] || summary="gate.sh exited 1 and its output could not be read"

if [ "$GATE_FOR" = release ]; then
  # untagged_pr is a warning and never reaches this list, so what fires here is an
  # unconfirmed branch flow, an unparseable artifact, or a missing manifest.
  deny "gate.sh --for release blocks this promotion. Release directory: $ARTIFACTS. Blockers: $summary. Fix what is named and rerun; do not open the release PR until gate.sh exits 0."
fi

deny "gate.sh --for pr blocks this PR. Artifacts: $ARTIFACTS. Blockers: $summary. Produce the missing evidence through artifact.sh and rerun; do not open the PR until gate.sh exits 0."
