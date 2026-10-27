"use strict";
// bash-allowlist.js - the logic behind hooks/bash-allowlist.sh: odoo-dev-tester is
// read-only for real. The tester has no Edit or Write, but a shell writes files well,
// so this is an ALLOWLIST (is this one of the commands the tester is supposed to
// run?) and not a blocklist of write patterns.
//
// It engages ONLY when the payload agent_type is odoo-dev-tester. Every other agent
// and the main session pass with no output. Fail closed inside that scope: an
// unreadable payload that names the tester is denied, one that does not is allowed.
const { raw, payload, toolName, agentType, command, deny: emit } = require("./parse-payload");

const TESTER = "odoo-dev-tester";

// Names, not paths: the basename is stable across $CLAUDE_PLUGIN_ROOT and skill-relative
// invocations. session-db.sh only wraps run-tests.sh with a session suffix.
const SCRIPTS = new Set(["artifact.sh", "run-tests.sh", "session-db.sh", "browser-ensure.sh", "module-classify.sh"]);
// Read-only git, written down explicitly. Anything not listed is denied.
const GIT_SUBCOMMANDS = new Set(["status", "diff", "log", "show", "rev-parse", "ls-files", "branch", "worktree", "remote", "cat-file"]);
// `git branch` with a name ARGUMENT creates a branch, so only these flags are accepted.
const BRANCH_FLAGS = new Set([
  "-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose", "-l", "--list", "--show-current",
  "--merged", "--no-merged", "--contains", "--no-contains", "--points-at", "--sort", "--format",
  "--color", "--no-color",
]);

const ALLOWED =
  "odoo-dev-tester may run exactly these, one command per Bash call: artifact.sh, " +
  "run-tests.sh, session-db.sh, browser-ensure.sh, module-classify.sh, and read-only git " +
  "(status, diff, log, show, rev-parse, ls-files, branch, worktree list, remote -v, " +
  "cat-file). No chaining, no pipes, no command substitution, and redirection only " +
  "to /dev/null. Name a script by its path, because a bare $VAR cannot be resolved " +
  "by this hook. If a file has to change, report it as a failure; do not fix it.";
const deny = (reason) => emit(reason + " " + ALLOWED);

const unquote = (t) => t.replace(/^["']+/, "").replace(/["']+$/, "");
const base = (t) => { const u = unquote(t); return u.slice(u.lastIndexOf("/") + 1); };

const checkGit = (toks) => {
  let i = 1;
  while (i < toks.length && unquote(toks[i]).startsWith("-")) {
    const g = unquote(toks[i]);
    if (g === "-C") { i += 2; continue; }
    if (g === "--no-pager") { i += 1; continue; }
    deny("Refused: git " + g + " is not a permitted global option. Only -C <dir> and --no-pager are.");
  }
  const sub = i < toks.length ? unquote(toks[i]) : "";
  if (!GIT_SUBCOMMANDS.has(sub)) {
    deny("Refused: `git " + (sub || "<none>") + "` is not a read-only git subcommand. The permitted set is " +
         "status, diff, log, show, rev-parse, ls-files, branch, worktree list, remote -v, cat-file.");
  }
  const args = toks.slice(i + 1).map(unquote);
  if (args.some((a) => a === "--output" || a.startsWith("--output="))) deny("Refused: git --output writes a file.");
  if (sub === "branch") {
    for (const a of args) {
      if (!a.startsWith("-")) deny("Refused: `git branch " + a + "` creates or renames a branch. Only read-only branch listing is permitted.");
      if (!BRANCH_FLAGS.has(a.split("=")[0])) deny("Refused: `git branch " + a + "` is not a read-only branch flag.");
    }
  }
  if (sub === "worktree" && args[0] !== "list") deny("Refused: only `git worktree list` is permitted; worktree-ensure.sh owns the worktree.");
  if (sub === "remote" && args.length && !["-v", "--verbose"].includes(args[0])) deny("Refused: only `git remote` and `git remote -v` are permitted.");
};

try {
  if (payload === null) {
    if (raw.includes(TESTER)) deny("The odoo-dev PreToolUse allowlist could not parse this hook payload, so it cannot tell what would run. Denying rather than guessing.");
    process.exit(0);
  }
  if (!(agentType === TESTER || agentType.endsWith(":" + TESTER)) || toolName !== "Bash") process.exit(0);
  if (command === null) deny("This Bash call carried no tool_input.command, so the odoo-dev allowlist cannot tell what would run.");

  // Rule 1 (redirection). Only /dev/null targets are permitted, and they are stripped
  // FIRST so the metacharacter rule never sees the ampersand in `2>&1`: this is why the
  // hook is an allowlist. `existing-work.sh ... 2>/dev/null` is judged on its program.
  let s = command;
  const REDIR = [/(^|\s)2>&1(?=\s|$)/g, /(^|\s)&>>?\s*\/dev\/null(?=\s|$)/g, /(^|\s)[0-9]?>>?\s*\/dev\/null(?=\s|$)/g];
  for (let i = 0; i < 4; i += 1) {
    const before = s;
    for (const re of REDIR) s = s.replace(re, "$1 ");
    if (s === before) break;
  }
  // Rule 2 (one command, no substitution). `|` covers `||` and `&` covers `&&`.
  for (const tok of ["`", "$(", "<(", ";", "|", "&"]) {
    if (s.includes(tok)) deny("Refused: this command contains " + tok + ", so it is more than one command or it substitutes a command.");
  }
  if (/[\n\r]/.test(s)) deny("Refused: this command spans more than one line.");
  // Anything still redirecting is writing somewhere, or reading a file the hook cannot vouch for.
  if (/[<>]/.test(s)) deny("Refused: this command redirects somewhere other than /dev/null.");

  // Rule 3 (the first token). A `bash` or `sh` wrapper is dropped, because the
  // sanctioned scripts are invoked as `bash "$SCRIPTS/artifact.sh" put ...`.
  let toks = s.trim().split(/\s+/).filter(Boolean);
  if (toks.length && ["bash", "sh"].includes(base(toks[0]))) toks = toks.slice(1);
  if (!toks.length) deny("Refused: this command names no program to run.");
  const head = base(toks[0]);
  if (head === "git") { checkGit(toks); process.exit(0); }       // Rule 4
  if (SCRIPTS.has(head)) process.exit(0);                        // Rule 5
  deny("Refused: `" + head + "` is not on the odoo-dev-tester allowlist.");
} catch (e) {
  deny("The odoo-dev PreToolUse allowlist failed while checking this command (" + (e && e.message ? e.message : String(e)) + "), so it denied rather than guessing.");
}
