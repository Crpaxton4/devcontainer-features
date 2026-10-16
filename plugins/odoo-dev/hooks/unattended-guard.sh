#!/usr/bin/env bash
# unattended-guard.sh - PreToolUse hook that puts a refusal behind the irreversible
# lines of the unattended implementer's invariants file.
#
# Prose asks and a hook binds. prompts/unattended-invariants.md says "never
# force-push" at every turn, and a long unsupervised session still drifts, so the
# lines whose breakage cannot be undone are also enforced here, where the length of
# the session and the rest of the context do not matter.
#
# It denies, for odoo-dev-implementer only:
#   - git push in any force variant: --force, --force-with-lease, --force-if-includes,
#     -f (alone or inside a cluster such as -fu), and a +refspec,
#   - git push --all and --mirror, which carry protected branches with them,
#   - git push to a default or protected branch, named or implied by the current branch,
#   - gh pr merge, gh pr ready, gh pr review, directly or through gh-as-owner,
#   - sed -i, perl -i, tee and a shell redirect aimed at a file inside a git working
#     tree (a gitignored path and a path outside any tree pass),
#   - docker in any form, including docker-compose and docker compose.
#
# This is accident prevention, not a threat model: the container is not an isolation
# boundary. The command is split into simple commands and tokenised with quotes, and
# heredoc bodies and $( ) substitutions are read too, but a command built at run time
# out of a variable is out of reach, as it is for any pre-tool hook.
#
# Scope: it engages ONLY when the payload reports agent_type odoo-dev-implementer,
# the same way bash-allowlist.sh scopes itself to the tester. Every other agent and
# the main session pass through with no output. Fail closed inside that scope only.
#
# Input: the PreToolUse payload as JSON on stdin.
# Output: nothing to allow; the deny envelope on stdout to refuse.
# Exit codes: 0 always | 2 only if node is unavailable and the payload names the agent.
set -uo pipefail

payload="$(cat)"

if out="$(printf '%s' "$payload" | node -e '
const fs = require("fs"), path = require("path");
const { execFileSync } = require("child_process");
const AGENT = "odoo-dev-implementer";
const ADVICE = " This guard binds odoo-dev-implementer: it never force-pushes, never " +
  "marks, approves or merges a pull request, never pushes to a protected branch, " +
  "never edits a working tree through a shell, and never runs docker.";

const allow = () => process.exit(0);
const deny = (reason) => {
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: reason + ADVICE,
    },
  }));
  process.exit(0);
};

const PROTECTED = /^(main|master|dev|develop|staging|uat|production|prod|trunk|release\/.+)$/;
const WRAPPERS = new Set(["sudo", "env", "command", "exec", "nohup", "time", "builtin"]);
const base = (t) => { const i = t.lastIndexOf("/"); return i < 0 ? t : t.slice(i + 1); };

const git = (cwd, args) => {
  try {
    return execFileSync("git", ["-C", cwd, ...args],
      { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch { return null; }
};
const currentBranch = (cwd) => git(cwd, ["rev-parse", "--abbrev-ref", "HEAD"]);
const defaultBranch = (cwd) => {
  const r = git(cwd, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"]);
  return r ? r.replace(/^origin\//, "") : null;
};
const isProtected = (name, cwd) => {
  const n = name.replace(/^\+/, "").replace(/^refs\/heads\//, "");
  return PROTECTED.test(n) || (cwd && n === defaultBranch(cwd));
};

// Is this path inside a git working tree, and not gitignored?
const inTree = (target, cwd) => {
  if (/^\/dev\/(null|stdout|stderr|tty|fd\/\d+)$/.test(target)) return false;
  let abs = path.resolve(cwd, target);
  let dir = path.dirname(abs);
  while (!fs.existsSync(dir)) {
    const up = path.dirname(dir);
    if (up === dir) return false;
    dir = up;
  }
  if (git(dir, ["rev-parse", "--is-inside-work-tree"]) !== "true") return false;
  try {
    execFileSync("git", ["-C", dir, "check-ignore", "-q", abs], { stdio: "ignore" });
    return false;                              // exit 0: ignored, so not a tracked path
  } catch { return true; }
};

// Remove heredoc bodies so their text is not read as commands.
const stripHeredocs = (s) => {
  const lines = s.split("\n"), out = [];
  let end = null;
  for (const line of lines) {
    if (end !== null) { if (line.trim() === end) end = null; continue; }
    out.push(line);
    const m = line.match(/<<-?\s*(["\x27]?)([A-Za-z_][A-Za-z0-9_]*)\1/);
    if (m) end = m[2];
  }
  return out.join("\n");
};

// Split into simple commands, each a list of {t, q} tokens; q marks a quoted token.
const parse = (s) => {
  const segs = [];
  let toks = [], cur = "", q = false, quote = null, has = false;
  const endTok = () => { if (has) toks.push({ t: cur, q }); cur = ""; q = false; has = false; };
  const endSeg = () => { endTok(); if (toks.length) segs.push(toks); toks = []; };
  for (let i = 0; i < s.length; i += 1) {
    const c = s[i];
    if (quote) {
      if (c === quote) quote = null;
      else if (c === "\\" && quote === "\"" && i + 1 < s.length) { cur += s[i + 1]; i += 1; }
      else cur += c;
      continue;
    }
    if (c === "\"" || c === "\x27") { quote = c; q = true; has = true; continue; }
    if (c === "\\" && i + 1 < s.length) { cur += s[i + 1]; has = true; i += 1; continue; }
    if (/\s/.test(c) && c !== "\n") { endTok(); continue; }
    if (c === "\n" || c === ";" || c === "(" || c === ")") { endSeg(); continue; }
    if (c === "|") { endSeg(); if (s[i + 1] === "|") i += 1; continue; }
    if (c === "&") {
      if (s[i - 1] === ">" || s[i + 1] === ">") { cur += c; has = true; continue; }
      endSeg(); if (s[i + 1] === "&") i += 1; continue;
    }
    cur += c; has = true;
  }
  endSeg();
  return segs;
};

// Nested commands: $( ... ) and backticks.
const nested = (s) => {
  const out = [];
  for (const m of s.matchAll(/\$\(([^()]*)\)/g)) out.push(m[1]);
  for (const m of s.matchAll(/`([^`]*)`/g)) out.push(m[1]);
  return out;
};

const checkGit = (args, cwd) => {
  let i = 0, dir = cwd;
  while (i < args.length && args[i].startsWith("-")) {
    if (args[i] === "-C" && args[i + 1]) { dir = path.resolve(dir, args[i + 1]); i += 2; continue; }
    if (args[i] === "-c" || args[i] === "--git-dir" || args[i] === "--work-tree") { i += 2; continue; }
    i += 1;
  }
  if (args[i] !== "push") return;
  checkPush(args.slice(i + 1), dir);
};

const checkPush = (args, dir) => {
  const pos = [];
  for (const a of args) {
    if (a === "--force" || a.startsWith("--force-with-lease") || a.startsWith("--force-if-includes") ||
        (/^-[A-Za-z]+$/.test(a) && a.includes("f"))) {
      deny("Refused: a force push in any variant is never allowed (" + a + ").");
    }
    if (a === "--all" || a === "--mirror") {
      deny("Refused: git push " + a + " carries protected branches with it.");
    }
    if (a.startsWith("-")) continue;
    pos.push(a);
  }
  const refs = pos.slice(1);                    // pos[0] is the remote
  for (const r of refs) {
    if (r.startsWith("+")) deny("Refused: the +refspec " + r + " is a force push.");
    const dest = r.includes(":") ? r.slice(r.lastIndexOf(":") + 1) : r;
    const name = dest === "HEAD" ? (currentBranch(dir) || "") : dest.replace(/^refs\/heads\//, "");
    if (name && isProtected(name, dir)) {
      deny("Refused: pushing to the default or protected branch " + name + ".");
    }
  }
  if (refs.length === 0) {
    const cur = currentBranch(dir);
    if (cur && isProtected(cur, dir)) {
      deny("Refused: this push would send the default or protected branch " + cur + ".");
    }
  }
};

const checkGh = (args) => {
  const pos = [];
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "-R" || args[i] === "--repo") { i += 1; continue; }
    if (args[i].startsWith("-")) continue;
    pos.push(args[i]);
  }
  if (pos[0] === "pr" && ["merge", "ready", "review"].includes(pos[1])) {
    deny("Refused: gh pr " + pos[1] + " is never allowed unattended. A human merges, " +
         "readies and reviews.");
  }
};

const checkRedirect = (toks, cwd) => {
  for (let i = 0; i < toks.length; i += 1) {
    if (toks[i].q) continue;
    const m = toks[i].t.match(/^(?:\d*|&)(>>?|>\|)(.*)$/);
    if (!m) continue;
    let target = m[2];
    if (target.startsWith("&")) continue;       // 2>&1 and >&2 duplicate a descriptor
    if (target === "" && toks[i + 1]) { target = toks[i + 1].t; i += 1; }
    if (target && inTree(target, cwd)) {
      deny("Refused: a shell redirect into " + target + ", a file inside a git working " +
           "tree. Use the Edit or Write tool, which leaves a reviewable diff.");
    }
  }
};

const checkSimple = (toks, cwd, depth) => {
  checkRedirect(toks, cwd);
  let w = toks.map((x) => x.t);
  while (w.length && (/^[A-Za-z_][A-Za-z0-9_]*=/.test(w[0]) || WRAPPERS.has(base(w[0])))) w = w.slice(1);
  if (!w.length) return;
  const head = base(w[0]);
  const args = w.slice(1);

  if (head === "docker" || head === "docker-compose" || head === "podman") {
    deny("Refused: docker in any form. The container is already the environment.");
  }
  if (["bash", "sh", "zsh", "eval"].includes(head)) {
    const k = head === "eval" ? 0 : args.indexOf("-c") + 1;
    if ((head === "eval" || k > 0) && args[head === "eval" ? 0 : k] !== undefined && depth < 4) {
      analyse(args.slice(head === "eval" ? 0 : k).join(" "), cwd, depth + 1);
    }
    return;
  }
  if (head === "git") { checkGit(args, cwd); return; }
  if (head === "gh") { checkGh(args); return; }
  if (/^gh-as-owner(\.sh)?$/.test(head)) {
    if (/^pr-(merge|ready|review)$/.test(args[0] || "")) {
      deny("Refused: gh-as-owner " + args[0] + " is never allowed unattended.");
    }
    if (args[0] === "gh") checkGh(args.slice(1));
    if (args[0] === "push") {                    // push <worktree> <branch> [flags]
      const rest = args.slice(1);
      const dir = rest.find((a) => !a.startsWith("-")) || cwd;
      checkPush(["origin", ...rest.filter((a) => a.startsWith("-")),
                 ...rest.filter((a) => !a.startsWith("-")).slice(1)], path.resolve(cwd, dir));
    }
    return;
  }
  if (head === "sed" || head === "perl") {
    const inplace = args.some((a) =>
      a === "--in-place" || a.startsWith("--in-place=") || (head === "perl" && /^-[A-Za-z]*i/.test(a)) ||
      (head === "sed" && /^-[A-Za-z]*i/.test(a)));
    if (inplace) {
      let files = args.filter((a) => !a.startsWith("-"));
      const scripted = args.some((a) => a === "-e" || a === "-f" || a.startsWith("--expression") || a.startsWith("--file"));
      if (!scripted) files = files.slice(1);     // the first operand is the script
      if (files.some((f) => inTree(f, cwd))) {
        deny("Refused: " + head + " -i edits a file inside a git working tree in place. " +
             "Use the Edit tool, which leaves a reviewable diff.");
      }
    }
    return;
  }
  if (head === "tee") {
    for (const f of args.filter((a) => !a.startsWith("-"))) {
      if (inTree(f, cwd)) {
        deny("Refused: tee writes " + f + ", a file inside a git working tree. Use the " +
             "Write tool, which leaves a reviewable diff.");
      }
    }
  }
};

function analyse(cmd, cwd, depth) {
  const body = stripHeredocs(cmd);
  for (const seg of parse(body)) checkSimple(seg, cwd, depth);
  for (const inner of nested(body)) {
    if (depth < 4) analyse(inner, cwd, depth + 1);
  }
}

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (d) => { raw += d; });
process.stdin.on("end", () => {
  let p = null;
  try { p = JSON.parse(raw); } catch { p = null; }
  if (!p || typeof p !== "object" || Array.isArray(p)) {
    if (raw.includes(AGENT)) deny("The unattended guard could not parse this hook payload, so it denied rather than guessing.");
    allow();
  }
  const type = typeof p.agent_type === "string" ? p.agent_type : "";
  if (!(type === AGENT || type.endsWith(":" + AGENT))) allow();
  if (p.tool_name !== "Bash") allow();
  try {
    const cmd = p.tool_input && typeof p.tool_input.command === "string" ? p.tool_input.command : null;
    if (cmd === null) deny("This Bash call carried no tool_input.command, so the unattended guard cannot tell what would run.");
    const cwd = typeof p.cwd === "string" && p.cwd ? p.cwd : process.cwd();
    analyse(cmd, cwd, 0);
    allow();
  } catch (e) {
    deny("The unattended guard failed while checking this command (" +
         (e && e.message ? e.message : String(e)) + "), so it denied rather than guessing.");
  }
});
' 2>/dev/null)"; then
  [ -n "$out" ] && printf '%s\n' "$out"
  exit 0
fi

# node is the one JSON dependency this plugin already has. If it is missing, a silent
# hook would hand the implementer an unchecked shell, so block instead.
case "$payload" in
  *odoo-dev-implementer*)
    echo "unattended-guard.sh: node is unavailable, so the odoo-dev-implementer guard cannot run. Denying rather than allowing an unchecked shell." >&2
    exit 2
    ;;
esac
exit 0
