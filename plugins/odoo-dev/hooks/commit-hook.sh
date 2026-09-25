#!/usr/bin/env bash
# commit-hook.sh — PreToolUse hook that enforces the manifest-version bump.
#
# This hook engages on exactly one shape: `git commit`. It enforces the one rule
# this plugin asks a session to remember on every single commit — bump the
# module's manifest version — which #799 measured decaying with turn depth while
# the rule text stays put. A commit that would carry changes to a module whose
# __manifest__.py version has not moved is DENIED.
#
# `git commit` is an ordinary command in every repository on the machine, so
# everything the hook cannot decide passes in silence: no module in the change
# set, no repository, a merge or rebase in progress, a module that does not exist
# at the base commit, or an explicit pathspec, which narrows the commit to a
# subset this hook does not try to reconstruct. That last one is the deliberate
# bypass: silence about work we cannot attribute beats denying it.
#
# THERE IS NO PR GATE HERE ANY MORE. This hook used to hold `pr-open.sh`,
# `release-pr.sh` and `gh pr create` against `gate.sh`, which read the chain's
# artifacts and refused the call when they were missing. #904 records what that
# cost: a finished, tested, reviewed branch could not open a pull request without
# artifacts only one particular agent chain produces, and the only way through was
# to hand-write the very files the gate existed to verify. The gate is gone, the
# artifacts stay as optional evidence, and opening a pull request is now close to
# unconditional. The manifest rule survives because it depends on no artifact at
# all: the evidence for it is the repository in front of it.
#
# A shape is matched in command position, never as a substring of the command
# text. Heredoc bodies are dropped first, because the body of a heredoc is data
# rather than commands: a commit message that happens to name a module is a
# commit message. What is left is split into commands on the shell operators
# `;`, `&&`, `||`, `|`, `&` and the newline, and each command is judged by its
# first token alone, with a leading `env`, `bash`, `sh`, `command`, `sudo` or
# `VAR=value` prefix stripped so that a wrapped call still reads as the call it
# is. A name that appears later in a command is an argument, and an argument is
# not an invocation, so `git commit -m "docs: explain git commit"` is one commit
# and not two.
#
# Input: the PreToolUse payload as JSON on stdin.
# Output: nothing at all to allow; the deny envelope on stdout to refuse.
# Exit codes: 0 always (allow and deny both ride on exit 0 with JSON) | 2 only if
#             node is available to parse but not to build the deny envelope.
set -uo pipefail

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
  echo "commit-hook.sh: $1" >&2
  exit 2
}

# Is this a `git commit`, and which repository would it write to? Parsing lives in
# node because the command is a JSON string and the quoting rules are its own.
if ! fields="$(printf '%s' "$payload" | node -e '
const SQ = "\x27";
const unquote = (t) => t.replace(/^["\x27]+/, "").replace(/["\x27]+$/, "");
const base = (t) => { const u = unquote(t); const i = u.lastIndexOf("/"); return i < 0 ? u : u.slice(i + 1); };
// join(dir, arg) — `git -C <arg>` resolved against the directory it runs in. An
// empty arg resolves to nothing rather than to the directory itself, because
// `git -C ""` is a call the hook cannot attribute to any repository.
const join = (dir, arg) => (!arg ? "" : arg.startsWith("/") ? arg : !dir ? "" : dir.replace(/\/+$/, "") + "/" + arg);

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
// not commands, so nothing inside one can name a command this hook judges.
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

  // A payload we cannot read names no commit either, so there is nothing to judge.
  // The allowlist hook is the one that fails closed on an unreadable payload.
  if (!p || typeof p !== "object" || Array.isArray(p)) return emit("none", "", "");
  if (p.tool_name !== "Bash") return emit("none", "", "");
  const cmd = p.tool_input && typeof p.tool_input.command === "string" ? p.tool_input.command : "";
  const cwd = typeof p.cwd === "string" ? p.cwd : "";
  const cmds = commandsIn(cmd, 0);

  // The one shape: `git commit`. The repository is the payload cwd unless a leading
  // `-C` moves it; `--git-dir` and `--work-tree` move it somewhere this hook
  // cannot follow, and a commit it cannot locate is one it says nothing about.
  // Only the flags that change WHAT the commit would carry are reported: `-a`
  // widens it to every tracked modification, `--amend` rewrites the tip so the
  // comparison base is its parent. An explicit pathspec narrows the commit to a
  // subset the hook does not attempt to reconstruct, and narrows it in silence.
  const VALUED = ["-m", "--message", "-F", "--file", "-C", "--reuse-message",
    "-c", "--reedit-message", "--fixup", "--squash", "--author", "--date",
    "-t", "--template", "--trailer", "--pathspec-from-file"];
  // A short cluster ends in the only letter that can take the next token.
  const CLUSTER = /^-[A-Za-z]+$/;
  const TAKES = "mFCct";
  for (const call of cmds) {
    if (base(call[0]) !== "git") continue;
    let repo = cwd;
    let opaque = false;
    let i = 1;
    for (; i < call.length; i += 1) {
      const a = call[i];
      if (!a.startsWith("-")) break;
      if (a === "-C") { repo = join(repo, call[i + 1] || ""); i += 1; continue; }
      if (a === "-c") { i += 1; continue; }
      if (a === "--git-dir" || a === "--work-tree" || a === "--namespace") { opaque = true; i += 1; continue; }
      if (/^--(git-dir|work-tree|namespace)=/.test(a)) { opaque = true; continue; }
    }
    if (call[i] !== "commit") continue;
    if (opaque || !repo) return emit("none", "", "");
    const flags = [];
    let pathspec = false;
    for (let j = i + 1; j < call.length; j += 1) {
      const a = call[j];
      if (a === "--") { pathspec = j + 1 < call.length; break; }
      if (!a.startsWith("-")) { pathspec = true; break; }
      if (a === "--amend") { flags.push("amend"); continue; }
      if (a === "--all") { flags.push("all"); continue; }
      if (VALUED.indexOf(a) >= 0) { j += 1; continue; }
      if (!CLUSTER.test(a)) continue;
      // A short cluster is boolean flags until one that takes a value, and that
      // one swallows the rest of its own token or, if it ends it, the next one.
      // `-ma` is therefore `-m a` and carries no `-a`, which is why the letters
      // are read in order rather than searched for.
      for (let k = 1; k < a.length; k += 1) {
        if (TAKES.indexOf(a[k]) >= 0) { if (k === a.length - 1) j += 1; break; }
        if (a[k] === "a") flags.push("all");
      }
    }
    if (pathspec) return emit("none", "", "");
    return emit("commit", repo, flags.join(" "));
  }
  return emit("none", "", "");
});
' 2>/dev/null)"; then
  # Without node there is no parser, so there is no change set to read and nothing
  # to say about the commit. `git commit` is an ordinary command in every
  # repository on the machine and this hook never denies what it cannot decide, so
  # an unparseable payload passes exactly like an unattributable one.
  exit 0
fi

mode="$(printf '%s\n' "$fields" | sed -n 1p)"
worktree="$(printf '%s\n' "$fields" | sed -n 2p)"
note="$(printf '%s\n' "$fields" | sed -n 3p)"

[ "$mode" = "commit" ] || exit 0

# manifest_version — the version string of an Odoo manifest read on stdin, or
# nothing when there is no readable one. Same expression module-classify.sh uses,
# so what this hook calls a bump and what the rest of the plugin calls a bump are
# the same thing. Both quote styles appear in the wild; the first `version` key wins.
manifest_version() {
  node -e '
let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (d) => { raw += d; });
process.stdin.on("end", () => {
  const m = /["\x27]version["\x27]\s*:\s*["\x27]([^"\x27]+)["\x27]/.exec(raw);
  process.stdout.write(m ? m[1] : "");
});
' 2>/dev/null
}

# A `git commit` carrying module changes with the manifest version
# standing still. This is the one rule in the plugin that a session is asked to
# remember on every commit, and #799 measured what remembering is worth — the
# obligation decays with turn depth while the rule text does not change. So it
# stops being an obligation here and becomes a denial at the tool boundary.
#
# A module is a directory carrying __manifest__.py, the same test
# module-classify.sh uses, found by walking up from each changed path. Everything
# the hook cannot decide passes in silence, because `git commit` is an ordinary
# command in every repository on the machine and this plugin must not police work
# it does not own. It says nothing when: the directory is not a work tree, a
# merge, rebase, cherry-pick or revert is in progress, the base commit does not
# exist, nothing about a module is being committed, the module is not present at
# the base (a first commit has no earlier version to move away from), the module's
# manifest is not in the commit at all (a removal bumps nothing), or either
# manifest has no readable version key.
repo="$worktree"
amend=no; all=no
case " $note " in *" amend "*) amend=yes ;; esac
case " $note " in *" all "*) all=yes ;; esac

[ -d "$repo" ] || exit 0
git -C "$repo" rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0
gitdir="$(git -C "$repo" rev-parse --absolute-git-dir 2>/dev/null)" || exit 0
[ -n "$gitdir" ] || exit 0
for marker in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD rebase-merge rebase-apply; do
  [ -e "$gitdir/$marker" ] && exit 0
done

# --amend replaces the tip, so what the commit would carry is measured against
# the tip's parent. Amending a root commit has no parent and is left alone.
base_rev=HEAD
[ "$amend" = yes ] && base_rev='HEAD^'
git -C "$repo" rev-parse --verify -q "$base_rev^{commit}" >/dev/null 2>&1 || exit 0

# The index is what `git commit` writes; `-a` adds every tracked modification
# in the work tree on top of it, and neither form picks up an untracked file.
changed="$(git -C "$repo" diff --cached --name-only "$base_rev" 2>/dev/null)"
if [ "$all" = yes ]; then
  changed="$changed
$(git -C "$repo" diff --name-only "$base_rev" 2>/dev/null)"
fi

modules=""
while IFS= read -r path; do
  [ -n "$path" ] || continue
  dir="$(dirname "$path")"
  while [ "$dir" != "." ] && [ "$dir" != "/" ] && [ -n "$dir" ]; do
    if [ -f "$repo/$dir/__manifest__.py" ] \
       || git -C "$repo" cat-file -e "$base_rev:$dir/__manifest__.py" 2>/dev/null; then
      modules="$modules$dir
"
      break
    fi
    dir="$(dirname "$dir")"
  done
done <<EOF
$changed
EOF
modules="$(printf '%s' "$modules" | sed '/^$/d' | sort -u)"
[ -n "$modules" ] || exit 0

stale=""
while IFS= read -r mod; do
  [ -n "$mod" ] || continue
  old="$(git -C "$repo" show "$base_rev:$mod/__manifest__.py" 2>/dev/null)"
  [ -n "$old" ] || continue
  if [ "$all" = yes ]; then
    new=""
    [ -f "$repo/$mod/__manifest__.py" ] && new="$(cat "$repo/$mod/__manifest__.py" 2>/dev/null)"
  else
    new="$(git -C "$repo" show ":$mod/__manifest__.py" 2>/dev/null)"
  fi
  [ -n "$new" ] || continue
  oldv="$(printf '%s' "$old" | manifest_version)"
  newv="$(printf '%s' "$new" | manifest_version)"
  [ -n "$oldv" ] && [ -n "$newv" ] || continue
  [ "$oldv" != "$newv" ] && continue
  stale="$stale, $mod (still $oldv)"
done <<EOF
$modules
EOF

[ -n "$stale" ] || exit 0
deny "The odoo-dev commit hook blocks this commit: it changes ${stale#, } without moving the manifest version. Run \`bump_manifest_version.py <module>\` from skills/odoo-devcontainer/scripts/ and stage the manifest, then commit again. An unbumped module deploys and then does nothing until someone runs -u by hand, and the commit is the last place that is cheap to fix."
