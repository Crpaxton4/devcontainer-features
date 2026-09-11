#!/usr/bin/env bash
# bash-allowlist.sh — PreToolUse hook that makes odoo-dev-tester read-only for real.
#
# The tester carries `disallowedTools: Edit, Write, NotebookEdit`, so the obvious
# way to change a file is gone. Bash is not gone, because the agent needs it to run
# `run-tests.sh` and `artifact.sh` — and a shell writes files perfectly well. This
# hook is what turns "does not edit code" from a strong default into a property of
# the harness.
#
# ALLOWLIST, NOT BLOCKLIST. A write-pattern blocklist gets this exactly backwards
# in both directions: it denies `existing-work.sh ... 2>/dev/null` because the
# string contains `>`, and it waves through `python -c "open(f,w)"` because the
# string contains nothing it recognises. So the question this hook asks is not
# "does this look like a write?" but "is this one of the handful of commands the
# tester is supposed to run?".
#
# Scope: it engages ONLY when the PreToolUse payload reports agent_type
# odoo-dev-tester. Every other agent and the main session pass through untouched,
# with no output at all. This ships inside a plugin that loads in real sessions;
# policing anything beyond the one agent it was written for would be a bug.
#
# Fail closed, but only inside that scope. An unreadable payload that names the
# tester is denied; an unreadable payload that does not is allowed, because a hook
# must never block work it was not asked to police.
#
# Input: the PreToolUse payload as JSON on stdin.
# Output: nothing at all to allow; the deny envelope on stdout to refuse.
# Exit codes: 0 always (allow and deny both ride on exit 0 with JSON) | 2 only if
#             node itself is unavailable, which blocks with the reason on stderr.
set -uo pipefail

payload="$(cat)"

if out="$(printf '%s' "$payload" | node -e '
const TESTER = "odoo-dev-tester";

// The five sanctioned scripts. Names, not paths: the tester invokes them through
// $CLAUDE_PLUGIN_ROOT or a skill-relative path, and the basename is the part that
// is stable across both.
const SCRIPTS = new Set([
  "artifact.sh", "run-tests.sh", "browser-ensure.sh", "gate.sh", "module-classify.sh",
]);

// Read-only git, written down explicitly. Anything not on this list is denied,
// including anything whose write behaviour is merely unclear.
const GIT_SUBCOMMANDS = new Set([
  "status", "diff", "log", "show", "rev-parse", "ls-files", "branch", "worktree",
  "remote", "cat-file",
]);

// `git branch` with a name ARGUMENT creates a branch, so only flags are accepted,
// and only these flags.
const BRANCH_FLAGS = new Set([
  "-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose", "-l", "--list",
  "--show-current", "--merged", "--no-merged", "--contains", "--no-contains",
  "--points-at", "--sort", "--format", "--color", "--no-color",
]);

const ALLOWED =
  "odoo-dev-tester may run exactly these, one command per Bash call: artifact.sh, " +
  "run-tests.sh, browser-ensure.sh, gate.sh, module-classify.sh, and read-only git " +
  "(status, diff, log, show, rev-parse, ls-files, branch, worktree list, remote -v, " +
  "cat-file). No chaining, no pipes, no command substitution, and redirection only " +
  "to /dev/null. Name a script by its path, because a bare $VAR cannot be resolved " +
  "by this hook. If a file has to change, that is a failure to report, not to fix: " +
  "hand it back to odoo-dev-builder or odoo-dev-upgrader.";

const allow = () => process.exit(0);
const deny = (reason) => {
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: reason,
    },
  }));
  process.exit(0);
};

const unquote = (t) => t.replace(/^["\x27]+/, "").replace(/["\x27]+$/, "");
const base = (t) => { const u = unquote(t); const i = u.lastIndexOf("/"); return i < 0 ? u : u.slice(i + 1); };

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (d) => { raw += d; });
process.stdin.on("end", () => {
  let p = null;
  try { p = JSON.parse(raw); } catch { p = null; }

  // An unparseable payload tells us nothing about who is calling. Fail closed only
  // if the raw text names the tester; otherwise get out of the way.
  if (!p || typeof p !== "object" || Array.isArray(p)) {
    if (raw.includes(TESTER)) {
      deny("The odoo-dev PreToolUse allowlist could not parse this hook payload, so it " +
           "cannot tell what would run. Denying rather than guessing. " + ALLOWED);
    }
    allow();
  }

  const type = typeof p.agent_type === "string" ? p.agent_type : "";
  const engaged = type === TESTER || type.endsWith(":" + TESTER);
  if (!engaged) allow();
  if (p.tool_name !== "Bash") allow();

  try {
    const cmd = p.tool_input && typeof p.tool_input.command === "string"
      ? p.tool_input.command : null;
    if (cmd === null) {
      deny("This Bash call carried no tool_input.command, so the odoo-dev allowlist " +
           "cannot tell what would run. " + ALLOWED);
    }

    // Rule 1 (redirection). Permitted redirections target /dev/null and nothing
    // else, and they are stripped FIRST so the metacharacter rule below never sees
    // the ampersand in `2>&1`. This is the whole reason the hook is an allowlist:
    // `existing-work.sh ... 2>/dev/null` must be judged on `existing-work.sh`, not
    // on the fact that it contains a greater-than sign.
    let s = cmd;
    const REDIR = [
      /(^|\s)2>&1(?=\s|$)/g,
      /(^|\s)&>>?\s*\/dev\/null(?=\s|$)/g,
      /(^|\s)[0-9]?>>?\s*\/dev\/null(?=\s|$)/g,
    ];
    for (let i = 0; i < 4; i += 1) {
      const before = s;
      for (const re of REDIR) s = s.replace(re, "$1 ");
      if (s === before) break;
    }

    // Rule 2 (one command, no substitution). This is what stops
    // `artifact.sh put x; rm -rf /tmp/y` from riding in on an allowlisted first
    // token. `|` covers `||` and `&` covers `&&`, so the reason names the exact
    // character that was found.
    for (const tok of ["`", "$(", "<(", ";", "|", "&"]) {
      if (s.includes(tok)) {
        deny("Refused: this command contains " + tok + ", so it is more than one " +
             "command or it substitutes a command. " + ALLOWED);
      }
    }
    if (/[\n\r]/.test(s)) {
      deny("Refused: this command spans more than one line. " + ALLOWED);
    }

    // Anything still redirecting after the /dev/null forms were removed is writing
    // somewhere, or reading from a file the hook cannot vouch for.
    if (/[<>]/.test(s)) {
      deny("Refused: this command redirects somewhere other than /dev/null. " + ALLOWED);
    }

    // Rule 3 (the first token). A `bash` or `sh` wrapper is dropped, because
    // `bash \"$SCRIPTS/artifact.sh\" put ...` is how the sanctioned scripts are
    // actually invoked; what follows the wrapper still has to earn its place.
    let toks = s.trim().split(/\s+/).filter(Boolean);
    if (toks.length && ["bash", "sh"].includes(base(toks[0]))) toks = toks.slice(1);
    if (!toks.length) {
      deny("Refused: this command names no program to run. " + ALLOWED);
    }
    const head = base(toks[0]);

    // Rule 4 (git).
    if (head === "git") {
      let i = 1;
      while (i < toks.length && unquote(toks[i]).startsWith("-")) {
        const g = unquote(toks[i]);
        if (g === "-C") { i += 2; continue; }
        if (g === "--no-pager") { i += 1; continue; }
        deny("Refused: git " + g + " is not a permitted global option. Only -C <dir> " +
             "and --no-pager are. " + ALLOWED);
      }
      const sub = i < toks.length ? unquote(toks[i]) : "";
      if (!GIT_SUBCOMMANDS.has(sub)) {
        deny("Refused: `git " + (sub || "<none>") + "` is not a read-only git " +
             "subcommand. The permitted set is status, diff, log, show, rev-parse, " +
             "ls-files, branch, worktree list, remote -v, cat-file. " + ALLOWED);
      }
      const args = toks.slice(i + 1).map(unquote);
      // --output turns a read into a write.
      if (args.some((a) => a === "--output" || a.startsWith("--output="))) {
        deny("Refused: git --output writes a file. " + ALLOWED);
      }
      if (sub === "branch") {
        for (const a of args) {
          if (!a.startsWith("-")) {
            deny("Refused: `git branch " + a + "` creates or renames a branch. Only " +
                 "read-only branch listing is permitted. " + ALLOWED);
          }
          if (!BRANCH_FLAGS.has(a.split("=")[0])) {
            deny("Refused: `git branch " + a + "` is not a read-only branch flag. " + ALLOWED);
          }
        }
      }
      if (sub === "worktree" && args[0] !== "list") {
        deny("Refused: only `git worktree list` is permitted; worktree-ensure.sh owns " +
             "the worktree. " + ALLOWED);
      }
      if (sub === "remote" && args.length && !["-v", "--verbose"].includes(args[0])) {
        deny("Refused: only `git remote` and `git remote -v` are permitted. " + ALLOWED);
      }
      allow();
    }

    // Rule 5.
    if (SCRIPTS.has(head)) allow();
    deny("Refused: `" + head + "` is not on the odoo-dev-tester allowlist. " + ALLOWED);
  } catch (e) {
    // An internal error while policing the tester is a deny, never a silent pass.
    deny("The odoo-dev PreToolUse allowlist failed while checking this command (" +
         (e && e.message ? e.message : String(e)) + "), so it denied rather than " +
         "guessing. " + ALLOWED);
  }
});
' 2>/dev/null)"; then
  [ -n "$out" ] && printf '%s\n' "$out"
  exit 0
fi

# node is the one JSON dependency this plugin already has. If it is missing, a hook
# that stays silent would quietly hand the tester an unrestricted shell, so block
# instead — exit 2 blocks the call and puts the reason in front of the agent.
case "$payload" in
  *odoo-dev-tester*)
    echo "bash-allowlist.sh: node is unavailable, so the odoo-dev-tester command allowlist cannot run. Denying rather than allowing an unchecked shell." >&2
    exit 2
    ;;
esac
exit 0
