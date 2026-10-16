#!/usr/bin/env bash
# unattended-guard.sh - PreToolUse hook that puts a refusal behind the irreversible
# lines of the unattended implementer's invariants file.
#
# Prose asks and a hook binds. prompts/unattended-invariants.md says "never
# force-push" at every turn, and a long unsupervised session still drifts, so the
# lines whose breakage cannot be undone are also enforced here, where the length of
# the session and the rest of the context do not matter.
#
# WHEN IT ENGAGES (two independent triggers, either one is enough)
#   1. The environment variable ODOO_UNATTENDED is set to any non-empty value. The
#      dispatcher sets it to 1 through --remote-env on the unattended claude command
#      line. A forked subagent inherits the session environment, so this covers the
#      forks of /odoo-dev:task, :test and :pr, where the pushes and the pull request
#      calls actually happen and where agent_type is odoo-dev-builder, -tester or
#      -pr rather than the implementer. It changes nothing for an interactive
#      session, because nothing sets it there. "0" and "false" are non-empty and
#      still engage the guard: set means on, unset means off.
#   2. The payload agent_type is odoo-dev-implementer, so an implementer session is
#      guarded even when the variable is missing.
# Every other call passes through with no output at all.
#
# WHAT IT DENIES
#   - git push in any force variant: --force, --force-with-lease, --force-if-includes,
#     -f (alone or inside a cluster such as -fu), and a +refspec,
#   - git push --all and --mirror, and any remote branch delete (--delete, -d, :ref),
#   - git push to a default or protected branch, named or implied by the current
#     branch (matched case-insensitively), with --repo and cd followed,
#   - gh pr merge, ready, review and create, the gh api merge and review routes, and
#     the GraphQL merge mutations, directly or through gh-as-owner (its pr-create
#     verb is the one sanctioned way to open a pull request),
#   - sed -i, perl -i, tee and a shell redirect aimed at a file inside a git working
#     tree (a gitignored path, a path outside any tree and an unresolvable ~ or $VAR
#     target pass),
#   - the same four writes aimed at the Claude configuration directory, whether or
#     not it is in a git tree: CLAUDE_CONFIG_DIR when set, and ~/.claude always. An
#     Edit or Write deny rule does not constrain Bash, so this is the route it closes.
#   - docker, docker-compose, podman, nerdctl, ctr and buildah in any form.
#
# HOW FAR IT SEES. The command is tokenised with quotes and redirect operators, split
# into simple commands, and looked at through shell keywords, wrappers (env, sudo,
# timeout, nice, stdbuf, setsid, xargs, find -exec and the like), bash -c in any flag
# cluster, $( ), backticks, aliases, here-strings, heredocs and pipes fed to a shell,
# and cd. This is accident prevention, not a threat model: a command built at run
# time from a variable, or a script file the shell is told to run, is out of reach
# for any pre-tool hook. A redirect whose target is an unset variable is not judged.
# The default branch is read from origin/HEAD only. Reading the project's real branch
# flow means resolving the repo map, which is not cheap in a hook that must answer in
# milliseconds, so the protected list is the common names plus origin/HEAD.
#
# Fail closed inside the scope only. Input: the PreToolUse payload as JSON on stdin.
# Output: nothing to allow; the deny envelope on stdout to refuse.
# Exit codes: 0 always | 2 only if node is unavailable and the guard is engaged.
set -uo pipefail

payload="$(cat)"

if out="$(printf '%s' "$payload" | node -e '
const fs = require("fs"), path = require("path");
const { execFileSync } = require("child_process");
const AGENT = "odoo-dev-implementer";
const RUN = (process.env.ODOO_UNATTENDED || "") !== "";
const ADVICE = " This guard binds unattended runs (ODOO_UNATTENDED set, or the " +
  "odoo-dev-implementer agent): they never force-push, never delete a remote branch, " +
  "never open, ready, review or merge a pull request by hand, never push to a " +
  "protected branch, never edit a working tree through a shell, and never run docker.";

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

// Names, plus Odoo series branches (19.0, 18.0, 17.0-dev, odoo-19.0) for the case where
// origin/HEAD does not name them. Task branches are <id>-<slug> and match none of these.
const PROTECTED = /^(main|master|dev|develop|staging|uat|production|prod|trunk|release\/.+|\d+\.\d.*|odoo.*)$/i;
const CONTAINERS = new Set(["docker", "docker-compose", "podman", "nerdctl", "ctr", "buildah"]);
const SHELLS = new Set(["bash", "sh", "zsh", "dash", "ksh"]);
const KEYWORDS = new Set(["if", "then", "else", "elif", "do", "while", "until", "!", "{", "}",
                          "time", "fi", "done", "esac"]);
// Wrappers that run the command after their own options. args: options that take a
// value; pos: positional operands to skip before the command (timeout DURATION).
const WRAP = {
  env: { args: ["-u", "-C", "-S", "--unset", "--chdir", "--split-string"], pos: 0 },
  sudo: { args: ["-u", "-g", "-h", "-p", "-C", "-r", "-t", "-U", "-D", "-R", "--user", "--group"], pos: 0 },
  doas: { args: ["-u", "-C"], pos: 0 },
  timeout: { args: ["-s", "-k", "--signal", "--kill-after"], pos: 1 },
  nice: { args: ["-n", "--adjustment"], pos: 0 },
  ionice: { args: ["-c", "-n", "-p", "-P", "-u"], pos: 0 },
  stdbuf: { args: ["-i", "-o", "-e"], pos: 0 },
  setsid: { args: [], pos: 0 }, nohup: { args: [], pos: 0 }, command: { args: [], pos: 0 },
  exec: { args: ["-a"], pos: 0 }, builtin: { args: [], pos: 0 }, unbuffer: { args: [], pos: 0 },
  xargs: { args: ["-I", "-n", "-P", "-d", "-L", "-E", "-s", "-a", "-l", "-i"], pos: 0 },
  flock: { args: ["-w", "-E", "-c"], pos: 1 }, chronic: { args: [], pos: 0 },
};
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
  const d = cwd ? defaultBranch(cwd) : null;
  return PROTECTED.test(n) || (d !== null && n.toLowerCase() === d.toLowerCase());
};

// Expand ~ and $VAR from this hook environment. null means "cannot be known", and an
// unknowable target is never judged to be inside a tree.
const expand = (p) => {
  let s = p;
  if (s === "~" || s.startsWith("~/")) {
    if (!process.env.HOME) return null;
    s = process.env.HOME + s.slice(1);
  } else if (s.startsWith("~")) return null;
  s = s.replace(/\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)/g,
    (m, a, b) => (process.env[a || b] !== undefined ? process.env[a || b] : m));
  return /[$`]/.test(s) ? null : s;
};

// Is this path inside the Claude configuration directory? Independent of git: the
// settings, hooks and permissions that govern an unattended session must not be
// rewritable by that session. CLAUDE_CONFIG_DIR is read first, because a relocated
// directory is not under ~/.claude; ~/.claude is always guarded as well. Symlinks are
// resolved on the nearest existing ancestor so a link into the directory is caught.
const inConfigDir = (target, cwd) => {
  const e = expand(target);
  if (e === null) return false;
  const roots = [];
  for (const r of [process.env.CLAUDE_CONFIG_DIR ? expand(process.env.CLAUDE_CONFIG_DIR) : null,
                   process.env.HOME ? path.join(process.env.HOME, ".claude") : null]) {
    if (!r) continue;
    roots.push(path.resolve(r));
    try { roots.push(fs.realpathSync(r)); } catch { /* not created yet */ }
  }
  const abs = path.resolve(cwd, e);
  const cands = [abs];
  let dir = abs;
  while (!fs.existsSync(dir)) {
    const up = path.dirname(dir);
    if (up === dir) break;
    dir = up;
  }
  try { cands.push(path.join(fs.realpathSync(dir), path.relative(dir, abs))); } catch { /* unreadable */ }
  return cands.some((c) => roots.some((r) => c === r || c.startsWith(r + path.sep)));
};
const CONFIG_ADVICE = " The Claude configuration directory holds the settings, hooks and " +
  "permissions that govern this session, and an unattended run never rewrites them.";

// Is this path inside a git working tree, and not gitignored?
const inTree = (target, cwd) => {
  const e = expand(target);
  if (e === null) return false;
  if (/^\/dev\/(null|stdout|stderr|tty|fd\/\d+)$/.test(e)) return false;
  const abs = path.resolve(cwd, e);
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

// Take heredoc bodies out of the text so they are not read as commands. Quote-aware,
// and a here-string (<<<) is not a heredoc. A body is returned for analysis only when
// the line that carries the operator also names a shell, because then the body is a
// script that the shell runs (bash <<EOT, cat <<EOT | bash).
const SHELL_LINE = /(^|[\s|;&(`\/])(bash|sh|zsh|dash|ksh)(\s|$)/;
const extractHeredocs = (s) => {
  let out = "", quote = null, pending = [], lineStart = 0;
  const bodies = [], subs = [];
  for (let i = 0; i < s.length;) {
    const c = s[i];
    // Inside $( ) the quote state starts afresh, so a heredoc whose << sits in a
    // $( ) that is itself inside double quotes is still a heredoc, and its body is
    // taken out instead of being read as commands.
    if (c === "$" && s[i + 1] === "(" && quote !== "\x27") {
      subs.push({ q: quote, d: 0 }); quote = null; out += "$("; i += 2; continue;
    }
    if (quote === null && subs.length && (c === "(" || c === ")")) {
      const top = subs[subs.length - 1];
      if (c === "(") top.d += 1;
      else if (top.d > 0) top.d -= 1;
      else quote = subs.pop().q;
      out += c; i += 1; continue;
    }
    if (quote) {
      if (c === "\\" && quote === "\"" && i + 1 < s.length) { out += s.slice(i, i + 2); i += 2; continue; }
      if (c === quote) quote = null;
      out += c; i += 1; continue;
    }
    if (c === "\"" || c === "\x27") { quote = c; out += c; i += 1; continue; }
    if (c === "\\") { out += s.slice(i, i + 2); i += 2; continue; }
    if (c === "<" && s[i + 1] === "<" && s[i + 2] !== "<" && s[i - 1] !== "<") {
      let j = i + 2;
      if (s[j] === "-") j += 1;
      while (s[j] === " " || s[j] === "\t") j += 1;
      let delim = "";
      if (s[j] === "\"" || s[j] === "\x27") {
        const q = s[j]; j += 1;
        while (j < s.length && s[j] !== q) { delim += s[j]; j += 1; }
        j += 1;
      } else {
        while (j < s.length && !/[\s;|&<>()]/.test(s[j])) { delim += s[j]; j += 1; }
      }
      if (delim) {
        pending.push(delim);
        out += s.slice(i, j); i = j; continue;
      }
    }
    if (c === "\n" && pending.length) {
      const line = out.slice(lineStart);
      out += "\n"; i += 1;
      for (const delim of pending) {
        const body = [];
        while (i < s.length) {
          let e = s.indexOf("\n", i); if (e < 0) e = s.length;
          const l = s.slice(i, e); i = Math.min(e + 1, s.length);
          if (l.trim() === delim) break;
          body.push(l);
        }
        if (SHELL_LINE.test(line)) bodies.push(body.join("\n"));
      }
      pending = []; lineStart = out.length; continue;
    }
    if (c === "\n") lineStart = out.length + 1;
    out += c; i += 1;
  }
  return { text: out, bodies };
};

// Split into simple commands. A token is {t, q} (q: it carried a quote), {redir: true}
// (a redirect to a file; the next token is the target), or {here: true} (a here-string;
// the next token is its text). seg.pipedFrom is the segment before a pipe.
const parse = (s) => {
  const segs = [];
  let toks = [], cur = "", q = false, quote = null, has = false, from = null, last = null;
  const endTok = () => { if (has) toks.push({ t: cur, q }); cur = ""; q = false; has = false; };
  const endSeg = (pipe) => {
    endTok();
    if (toks.length) { toks.pipedFrom = from; segs.push(toks); last = toks; }
    toks = []; from = pipe ? last : null;
  };
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
    if (c === "\n" || c === ";" || c === "(" || c === ")") { endSeg(false); continue; }
    if (c === "|") { endSeg(true); if (s[i + 1] === "|") { i += 1; from = null; } continue; }
    if (c === ">") {
      // A bare number before the operator is a descriptor (2>f), not an argument.
      if (has && !q && /^\d+$/.test(cur)) { cur = ""; has = false; } else endTok();
      let j = i + 1;
      if (s[j] === ">" || s[j] === "|") j += 1;
      if (s[j] === "&") {                      // >&2 duplicates a descriptor, no file
        j += 1;
        while (/[0-9-]/.test(s[j] || "")) j += 1;
      } else toks.push({ redir: true });
      i = j - 1; continue;
    }
    if (c === "<") {
      endTok();
      let j = i + 1;
      while (s[j] === "<") j += 1;
      if (j - i === 3) toks.push({ here: true });
      i = j - 1; continue;
    }
    if (c === "&") {
      if (s[i + 1] === ">") { cur = ""; has = false; continue; }   // &>file: the > follows
      endSeg(false); if (s[i + 1] === "&") i += 1; continue;
    }
    cur += c; has = true;
  }
  endSeg(false);
  return segs;
};

// Nested commands: $( ... ) and backticks.
// Quote-aware: a single-quoted region and an escaped backtick or dollar are text, not
// a substitution. An unescaped backtick or $( ) inside double quotes DOES run in a
// real shell, so it is read.
const nested = (s) => {
  const out = [];
  let dq = false;
  for (let i = 0; i < s.length; i += 1) {
    const c = s[i];
    if (c === "\\") { i += 1; continue; }
    if (!dq && c === "\x27") {
      const e = s.indexOf("\x27", i + 1);
      if (e < 0) break;
      i = e; continue;
    }
    if (c === "\"") { dq = !dq; continue; }
    if (c === "`") {
      let j = i + 1;
      while (j < s.length && s[j] !== "`") j += s[j] === "\\" ? 2 : 1;
      out.push(s.slice(i + 1, j));
      i = j; continue;
    }
    if (c === "$" && s[i + 1] === "(") {
      let depth = 1, j = i + 2;
      while (j < s.length && depth > 0) {
        if (s[j] === "\\") { j += 2; continue; }
        if (s[j] === "(") depth += 1;
        else if (s[j] === ")") depth -= 1;
        j += 1;
      }
      out.push(s.slice(i + 2, j - 1));
      i = j - 1; continue;
    }
  }
  return out;
};

const checkGit = (args, st) => {
  let i = 0, dir = st.cwd;
  while (i < args.length && args[i].startsWith("-")) {
    if (args[i] === "-C" && args[i + 1]) { dir = path.resolve(dir, expand(args[i + 1]) || "."); i += 2; continue; }
    if (args[i] === "-c" || args[i] === "--git-dir" || args[i] === "--work-tree") { i += 2; continue; }
    i += 1;
  }
  if (args[i] !== "push") return;
  checkPush(args.slice(i + 1), dir);
};

const PUSH_VALUE_OPTS = new Set(["-o", "--push-option", "--receive-pack", "--exec", "--signed"]);
const checkPush = (args, dir) => {
  const pos = [];
  let repo = null;
  for (let i = 0; i < args.length; i += 1) {
    const a = args[i];
    if (a === "--force" || a.startsWith("--force-with-lease") || a.startsWith("--force-if-includes") ||
        (/^-[A-Za-z]+$/.test(a) && a.includes("f"))) {
      deny("Refused: a force push in any variant is never allowed (" + a + ").");
    }
    if (a === "--all" || a === "--mirror") {
      deny("Refused: git push " + a + " carries protected branches with it.");
    }
    if (a === "--delete" || (/^-[A-Za-z]+$/.test(a) && a.includes("d"))) {
      deny("Refused: git push " + a + " deletes a remote branch, which cannot be undone.");
    }
    if (a === "--repo" && args[i + 1]) { repo = args[i + 1]; i += 1; continue; }
    if (a.startsWith("--repo=")) { repo = a.slice(7); continue; }
    if (PUSH_VALUE_OPTS.has(a)) { i += 1; continue; }
    if (a.startsWith("-")) continue;
    pos.push(a);
  }
  const refs = repo !== null ? pos : pos.slice(1);   // without --repo, pos[0] is the remote
  for (const r of refs) {
    if (r.startsWith("+")) deny("Refused: the +refspec " + r + " is a force push.");
    if (r.startsWith(":")) deny("Refused: the refspec " + r + " deletes a remote branch, which cannot be undone.");
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

const GQL = /mergePullRequest|enablePullRequestAutoMerge|markPullRequestReadyForReview|addPullRequestReview|submitPullRequestReview/;
const checkGh = (args) => {
  const pos = [];
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "-R" || args[i] === "--repo") { i += 1; continue; }
    if (args[i].startsWith("-")) continue;
    pos.push(args[i]);
  }
  if (pos[0] === "pr" && ["merge", "ready", "review", "close"].includes(pos[1])) {
    deny("Refused: gh pr " + pos[1] + " is never allowed unattended. A human merges, " +
         "readies, reviews and closes.");
  }
  if (pos[0] === "pr" && pos[1] === "create") {
    deny("Refused: a direct gh pr create. The odoo-dev:odoo-pr skill owns pull request " +
         "creation, through pr-open.sh or the gh-as-owner pr-create verb.");
  }
  if (pos[0] === "api") {
    // A review route is only refused when the call writes: reading reviews is fine.
    // -X M, -XM, --method M and --method=M all name the method.
    let method = "";
    for (let i = 0; i < args.length; i += 1) {
      const a = args[i];
      if (a === "-X" || a === "--method") method = args[i + 1] || "";
      else if (a.startsWith("--method=")) method = a.slice(9);
      else if (/^-X./.test(a)) method = a.slice(2).replace(/^=/, "");
    }
    method = method.toUpperCase();
    const writes = (method !== "" && method !== "GET") ||
      (method === "" && args.some((a) => /^(-f|-F|--field|--raw-field|--input)$/.test(a)));
    if (args.some((a) => /\/pulls\/[^/\s]*\/merge\b/.test(a) ||
                         (writes && /\/pulls\/[^/\s]*\/reviews?\b/.test(a)) || GQL.test(a))) {
      deny("Refused: this gh api call merges or reviews a pull request, which is the same " +
           "as gh pr merge or review.");
    }
    // A write to git/refs moves or deletes a branch; /merges merges one. Both are the
    // git-side force push, delete and merge that this guard already refuses.
    if (writes && args.some((a) => /\/git\/refs\b/.test(a) || /\/merges\b/.test(a))) {
      deny("Refused: this gh api call writes a git ref or merges a branch, which is the " +
           "same as a force push, a remote branch delete or a merge.");
    }
  }
};

const checkRedirects = (toks, cwd) => {
  for (let i = 0; i < toks.length; i += 1) {
    if (!toks[i].redir || !toks[i + 1] || toks[i + 1].redir || toks[i + 1].here) continue;
    const target = toks[i + 1].t;
    if (inConfigDir(target, cwd)) {
      deny("Refused: a shell redirect into " + target + ", inside the Claude configuration directory." + CONFIG_ADVICE);
    }
    if (inTree(target, cwd)) {
      deny("Refused: a shell redirect into " + target + ", a file inside a git working " +
           "tree. Use the Edit or Write tool, which leaves a reviewable diff.");
    }
  }
};

// Drop control keywords, assignments and wrapper options so the real command is the head.
const unwrap = (words) => {
  let w = words.slice();
  for (let guard = 0; guard < 20 && w.length; guard += 1) {
    if (KEYWORDS.has(w[0]) || /^[A-Za-z_][A-Za-z0-9_]*=/.test(w[0])) { w = w.slice(1); continue; }
    const spec = WRAP[base(w[0])];
    if (!spec) break;
    let i = 1, skip = spec.pos;
    while (i < w.length) {
      const a = w[i];
      if (a.startsWith("-") && a !== "-") { i += spec.args.includes(a) ? 2 : 1; continue; }
      if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(a) && base(w[0]) === "env") { i += 1; continue; }
      if (skip > 0) { skip -= 1; i += 1; continue; }
      break;
    }
    w = w.slice(i);
  }
  return w;
};

const checkSimple = (toks, st, depth) => {
  checkRedirects(toks, st.cwd);
  const here = toks.findIndex((x) => x.here);
  const hereText = here >= 0 && toks[here + 1] ? toks[here + 1].t : null;
  const words = toks.filter((x, i) => x.t !== undefined && !(i > 0 && toks[i - 1].redir) &&
                                      !(i > 0 && toks[i - 1].here) && !x.here).map((x) => x.t);
  const w = unwrap(words);
  if (!w.length) return;
  const head = base(w[0]);
  const args = w.slice(1);

  if (CONTAINERS.has(head)) {
    deny("Refused: " + head + " in any form. The container is already the environment.");
  }
  if (SHELLS.has(head) || head === "eval") {
    const ci = head === "eval" ? -1 : args.findIndex((a) => /^-[a-z]*c[a-z]*$/.test(a));
    if (head === "eval") { if (args.length && depth < 4) analyse(args.join(" "), st, depth + 1); }
    else if (ci >= 0 && args[ci + 1] !== undefined) { if (depth < 4) analyse(args[ci + 1], st, depth + 1); }
    else if (hereText !== null) { if (depth < 4) analyse(hereText, st, depth + 1); }
    else if (toks.pipedFrom && depth < 4) {
      // echo '...' | bash: the text of the producer is the script.
      const src = toks.pipedFrom.filter((x) => x.t !== undefined).map((x) => x.t);
      if (src.length > 1) analyse(src.slice(1).join(" "), st, depth + 1);
    }
    return;
  }
  if (head === "alias") {
    for (const a of args) {
      const k = a.indexOf("=");
      if (k > 0 && depth < 4) analyse(a.slice(k + 1), st, depth + 1);
    }
    return;
  }
  if (head === "cd" || head === "pushd") {
    const t = args.find((a) => !a.startsWith("-"));
    const e = t !== undefined ? expand(t) : null;
    if (e !== null) st.cwd = path.resolve(st.cwd, e);
    return;
  }
  if (head === "find") {
    for (let i = 0; i < args.length; i += 1) {
      if (["-exec", "-execdir", "-ok", "-okdir"].includes(args[i])) {
        let k = i + 1;
        while (k < args.length && args[k] !== ";" && args[k] !== "+") k += 1;
        checkSimple(args.slice(i + 1, k).map((t) => ({ t, q: false })), st, depth + 1);
      }
    }
    return;
  }
  if (head === "git") { checkGit(args, st); return; }
  if (head === "gh") { checkGh(args); return; }
  if (/^gh-as-owner(\.sh)?$/.test(head)) {
    if (/^pr-(merge|ready|review)$/.test(args[0] || "")) {
      deny("Refused: gh-as-owner " + args[0] + " is never allowed unattended.");
    }
    if (args[0] === "gh") checkGh(args.slice(1));
    if (args[0] === "push") {                    // push <worktree> <branch> [flags]
      const rest = args.slice(1);
      const dir = rest.find((a) => !a.startsWith("-")) || st.cwd;
      checkPush(["origin", ...rest.filter((a) => a.startsWith("-")),
                 ...rest.filter((a) => !a.startsWith("-")).slice(1)], path.resolve(st.cwd, dir));
    }
    return;
  }
  if (head === "sed" || head === "perl") {
    const inplace = args.some((a) =>
      a === "--in-place" || a.startsWith("--in-place=") || /^-[A-Za-z]*i/.test(a));
    if (inplace) {
      let files = args.filter((a) => !a.startsWith("-"));
      const scripted = args.some((a) => a === "-e" || a === "-f" || a.startsWith("--expression") || a.startsWith("--file"));
      if (!scripted) files = files.slice(1);     // the first operand is the script
      if (files.some((f) => inConfigDir(f, st.cwd))) {
        deny("Refused: " + head + " -i edits a file inside the Claude configuration directory." + CONFIG_ADVICE);
      }
      if (files.some((f) => inTree(f, st.cwd))) {
        deny("Refused: " + head + " -i edits a file inside a git working tree in place. " +
             "Use the Edit tool, which leaves a reviewable diff.");
      }
    }
    return;
  }
  if (head === "tee") {
    for (const f of args.filter((a) => !a.startsWith("-"))) {
      if (inConfigDir(f, st.cwd)) {
        deny("Refused: tee writes " + f + ", inside the Claude configuration directory." + CONFIG_ADVICE);
      }
      if (inTree(f, st.cwd)) {
        deny("Refused: tee writes " + f + ", a file inside a git working tree. Use the " +
             "Write tool, which leaves a reviewable diff.");
      }
    }
  }
};

function analyse(cmd, st, depth) {
  // A backslash-newline joins two lines into one command, before anything is read.
  const { text, bodies } = extractHeredocs(cmd.replace(/\\\r?\n/g, ""));
  for (const seg of parse(text)) checkSimple(seg, st, depth);
  for (const inner of nested(text)) {
    if (depth < 4) analyse(inner, { cwd: st.cwd }, depth + 1);
  }
  for (const b of bodies) {
    if (depth < 4) analyse(b, { cwd: st.cwd }, depth + 1);
  }
}

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (d) => { raw += d; });
process.stdin.on("end", () => {
  let p = null;
  try { p = JSON.parse(raw); } catch { p = null; }
  if (!p || typeof p !== "object" || Array.isArray(p)) {
    if (RUN || raw.includes(AGENT)) deny("The unattended guard could not parse this hook payload, so it denied rather than guessing.");
    allow();
  }
  const type = typeof p.agent_type === "string" ? p.agent_type : "";
  if (!(RUN || type === AGENT || type.endsWith(":" + AGENT))) allow();
  if (p.tool_name !== "Bash") allow();
  try {
    const cmd = p.tool_input && typeof p.tool_input.command === "string" ? p.tool_input.command : null;
    if (cmd === null) deny("This Bash call carried no tool_input.command, so the unattended guard cannot tell what would run.");
    const cwd = typeof p.cwd === "string" && p.cwd ? p.cwd : process.cwd();
    analyse(cmd, { cwd }, 0);
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
# hook would hand an unattended run an unchecked shell, so block instead.
if [ -n "${ODOO_UNATTENDED:-}" ]; then
  echo "unattended-guard.sh: node is unavailable, so the unattended guard cannot run. Denying rather than allowing an unchecked shell." >&2
  exit 2
fi
case "$payload" in
  *odoo-dev-implementer*)
    echo "unattended-guard.sh: node is unavailable, so the odoo-dev-implementer guard cannot run. Denying rather than allowing an unchecked shell." >&2
    exit 2
    ;;
esac
exit 0
