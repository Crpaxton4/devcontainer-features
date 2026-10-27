"use strict";
// unattended-guard.js - the logic behind hooks/unattended-guard.sh.
//
// Engages when ODOO_UNATTENDED is set (any value) or the payload agent_type is an
// odoo-dev-* agent (also namespaced, odoo-dev:odoo-dev-x). Every other call passes
// with no output. Fail closed inside that scope only.
//
// DENIES: force, delete, --all, --mirror and protected-branch git push | gh pr
// merge/ready/review/close/create, gh api merge/review/ref/protection writes (also via
// gh-as-owner) | sed -i, perl -i, tee and redirects into a git tree or the Claude config
// dir | docker | background runs | tree pinning | odoo upgrade, production, most ssh |
// Edit/Write outside a git tree and ${ODOO_DEV_HOME:-~/.config/odoo-dev} | every
// non-read-only tool once tasks/<task>/jobs/$ODOO_JOB_ID/STOP exists.
// ADVISORY (stderr, allowed): odoo -i/-u/--init/--update for odoo-dev-upgrade* agents.
// Accident prevention, not a threat model; the command is read through shell-parse.js.
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const { raw, payload, toolName, toolInput, agentType, command, cwd: PCWD, deny: emit } = require("./parse-payload");
const { base, extractHeredocs, parse, nested, unwrap } = require("./shell-parse");

const PREFIX = "odoo-dev-";
const RUN = (process.env.ODOO_UNATTENDED || "") !== "";
const ADVICE = " This guard binds unattended runs (ODOO_UNATTENDED set, or any " +
  "odoo-dev-* agent): they never force-push, never delete a remote branch, " +
  "never open, ready, review or merge a pull request by hand, never push to a " +
  "protected branch, never edit a working tree through a shell, and never run docker.";
const deny = (reason) => emit(reason + ADVICE);
const BG_MSG = "Refused: background runs die with the forked turn; run it in the foreground with a timeout.";
const PIN_ADVICE = " The addons path and the data dir are set by the environment, not by the agent.";
const CONFIG_ADVICE = " The Claude configuration directory holds the settings, hooks and " +
  "permissions that govern this session, and an unattended run never rewrites them.";
const HOME = process.env.HOME || "";
const STATE = path.resolve(process.env.ODOO_DEV_HOME || path.join(HOME, ".config/odoo-dev"));

// Task branches are <id>-<slug>; these names and Odoo series branches are protected.
const PROTECTED = /^(main|master|dev|develop|staging|uat|production|prod|trunk|release\/.+|\d+\.\d.*|odoo.*)$/i;
const CONTAINERS = new Set(["docker", "docker-compose", "podman", "nerdctl", "ctr", "buildah"]);
const SHELLS = new Set(["bash", "sh", "zsh", "dash", "ksh"]);
const ODOO_HEAD = /^odoo(\d+)?(-bin)?$/;
const WRITE_SQL = /\b(insert|update|delete|drop|alter|create|truncate|grant)\b/i;
const SSH_VALUE_OPTS = new Set(["-o", "-p", "-i", "-l", "-F", "-J", "-L", "-R", "-D", "-b", "-c", "-E", "-e", "-I", "-m", "-O", "-S", "-W", "-w", "-B", "-Q"]);
const PUSH_VALUE_OPTS = new Set(["-o", "--push-option", "--receive-pack", "--exec", "--signed"]);
const GQL = /mergePullRequest|enablePullRequestAutoMerge|markPullRequestReadyForReview|addPullRequestReview|submitPullRequestReview|createRef|updateRef|deleteRef|closePullRequest/;

const git = (cwd, args) => {
  try {
    return execFileSync("git", ["-C", cwd, ...args], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch { return null; }
};
const isProtected = (name, cwd) => {
  const n = name.replace(/^\+/, "").replace(/^refs\/heads\//, "");
  if (PROTECTED.test(n)) return true;
  const d = git(cwd, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"]);
  return d !== null && n.toLowerCase() === d.replace(/^origin\//, "").toLowerCase();
};

// Expand ~ and $VAR; null means unknowable, and such a target is never judged.
const expand = (p) => {
  let s = p;
  if (s === "~" || s.startsWith("~/")) { if (!HOME) return null; s = HOME + s.slice(1); }
  else if (s.startsWith("~")) return null;
  s = s.replace(/\$\{([A-Za-z_]\w*)\}|\$([A-Za-z_]\w*)/g, (m, a, b) => (process.env[a || b] !== undefined ? process.env[a || b] : m));
  return /[$`]/.test(s) ? null : s;
};

// Inside CLAUDE_CONFIG_DIR or ~/.claude, symlinks resolved on the nearest ancestor.
const inConfigDir = (target, cwd) => {
  const e = expand(target);
  if (e === null) return false;
  const roots = [];
  for (const r of [process.env.CLAUDE_CONFIG_DIR ? expand(process.env.CLAUDE_CONFIG_DIR) : null, HOME ? path.join(HOME, ".claude") : null]) {
    if (!r) continue;
    roots.push(path.resolve(r));
    try { roots.push(fs.realpathSync(r)); } catch { /* not created yet */ }
  }
  const abs = path.resolve(cwd, e);
  const cands = [abs];
  let dir = abs;
  while (!fs.existsSync(dir) && path.dirname(dir) !== dir) dir = path.dirname(dir);
  try { cands.push(path.join(fs.realpathSync(dir), path.relative(dir, abs))); } catch { /* unreadable */ }
  return cands.some((c) => roots.some((r) => c === r || c.startsWith(r + path.sep)));
};

// Inside a git working tree, and not gitignored?
const inTree = (target, cwd) => {
  const e = expand(target);
  if (e === null || /^\/dev\/(null|stdout|stderr|tty|fd\/\d+)$/.test(e)) return false;
  const abs = path.resolve(cwd, e);
  let dir = path.dirname(abs);
  while (!fs.existsSync(dir)) {
    if (path.dirname(dir) === dir) return false;
    dir = path.dirname(dir);
  }
  if (git(dir, ["rev-parse", "--is-inside-work-tree"]) !== "true") return false;
  try { execFileSync("git", ["-C", dir, "check-ignore", "-q", abs], { stdio: "ignore" }); return false; } catch { return true; }
};

// A path in an addons/<series> tree or the addons dir of an Odoo data dir.
const pinsTree = (p, cwd) => {
  const e = expand(p);
  if (e === null) return false;
  const abs = path.resolve(cwd, e) + "/";
  const roots = ["/var/lib/odoo"];
  if (process.env.ODOO_DEFAULT_DATA_DIR) roots.push(process.env.ODOO_DEFAULT_DATA_DIR.replace(/\/+$/, ""));
  return /\/addons\/\d+\.0\//.test(abs) || roots.some((r) => abs.startsWith(r + "/addons/"));
};

// File targets of a redirect, tee or sed -i; what names the write, such as "tee writes".
const checkTarget = (files, cwd, what, fix) => {
  let f = files.find((x) => base(x) === "odoo.conf");
  if (f !== undefined) deny("Refused: tree pinning. " + what + " " + f + ", which is odoo.conf." + PIN_ADVICE);
  f = files.find((x) => inConfigDir(x, cwd));
  if (f !== undefined) deny("Refused: " + what + " " + f + ", inside the Claude configuration directory." + CONFIG_ADVICE);
  f = files.find((x) => inTree(x, cwd));
  if (f !== undefined) deny("Refused: " + what + " " + f + ", a file inside a git working tree. Use the " + fix + " tool, which leaves a reviewable diff.");
};

const checkPush = (args, dir) => {
  const pos = [];
  let repo = null;
  for (let i = 0; i < args.length; i += 1) {
    const a = args[i];
    if (a === "--force" || a.startsWith("--force-with-lease") || a.startsWith("--force-if-includes") ||
        (/^-[A-Za-z]+$/.test(a) && a.includes("f"))) deny("Refused: a force push in any variant is never allowed (" + a + ").");
    if (a === "--all" || a === "--mirror") deny("Refused: git push " + a + " carries protected branches with it.");
    if (a === "--delete" || (/^-[A-Za-z]+$/.test(a) && a.includes("d"))) deny("Refused: git push " + a + " deletes a remote branch, which cannot be undone.");
    if (a === "--repo" && args[i + 1]) { repo = args[i + 1]; i += 1; continue; }
    if (a.startsWith("--repo=")) { repo = a.slice(7); continue; }
    if (PUSH_VALUE_OPTS.has(a)) { i += 1; continue; }
    if (!a.startsWith("-")) pos.push(a);
  }
  const refs = repo !== null ? pos : pos.slice(1);   // without --repo, pos[0] is the remote
  const cur = () => git(dir, ["rev-parse", "--abbrev-ref", "HEAD"]) || "";
  for (const r of refs) {
    if (r.startsWith("+")) deny("Refused: the +refspec " + r + " is a force push.");
    if (r.startsWith(":")) deny("Refused: the refspec " + r + " deletes a remote branch, which cannot be undone.");
    const dest = r.includes(":") ? r.slice(r.lastIndexOf(":") + 1) : r;
    const name = dest === "HEAD" ? cur() : dest.replace(/^refs\/heads\//, "");
    if (name && isProtected(name, dir)) deny("Refused: pushing to the default or protected branch " + name + ".");
  }
  if (refs.length === 0) {
    const c = cur();
    if (c && isProtected(c, dir)) deny("Refused: this push would send the default or protected branch " + c + ".");
  }
};

const checkGit = (args, st) => {
  let i = 0, dir = st.cwd;
  while (i < args.length && args[i].startsWith("-")) {
    if (args[i] === "-C" && args[i + 1]) { dir = path.resolve(dir, expand(args[i + 1]) || "."); i += 2; continue; }
    i += ["-c", "--git-dir", "--work-tree"].includes(args[i]) ? 2 : 1;
  }
  if (args[i] === "push") checkPush(args.slice(i + 1), dir);
};

const checkGh = (args) => {
  const pos = [];
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "-R" || args[i] === "--repo") { i += 1; continue; }
    if (!args[i].startsWith("-")) pos.push(args[i]);
  }
  if (pos[0] === "pr" && ["merge", "ready", "review", "close"].includes(pos[1])) {
    deny("Refused: gh pr " + pos[1] + " is never allowed unattended. A human merges, readies, reviews and closes.");
  }
  if (pos[0] === "pr" && pos[1] === "create") {
    deny("Refused: a direct gh pr create. The odoo-dev:odoo-pr skill owns pull request creation, through pr-open.sh or the gh-as-owner pr-create verb.");
  }
  if (pos[0] !== "api") return;
  let method = "";   // -X M, -XM, --method M and --method=M all name the method
  for (let i = 0; i < args.length; i += 1) {
    const a = args[i];
    if (a === "-X" || a === "--method") method = args[i + 1] || "";
    else if (a.startsWith("--method=")) method = a.slice(9);
    else if (/^-X./.test(a)) method = a.slice(2).replace(/^=/, "");
  }
  method = method.toUpperCase();
  const writes = (method !== "" && method !== "GET") ||
    (method === "" && args.some((a) => /^(-f|-F|--field|--raw-field|--input)$/.test(a)));
  const has = (re) => args.some((a) => re.test(a));
  if (has(/\/pulls\/[^/\s]*\/merge\b/) || (writes && has(/\/pulls\/[^/\s]*\/reviews?\b/)) || has(GQL)) {
    deny("Refused: this gh api call merges or reviews a pull request, which is the same as gh pr merge or review.");
  }
  if (writes && (has(/\/git\/refs\b/) || has(/\/merges\b/))) {
    deny("Refused: this gh api call writes a git ref or merges a branch, which is the same as a force push, a remote branch delete or a merge.");
  }
  if (writes && has(/\/branches\/.+\/protection\b/)) deny("Refused: this gh api call changes branch protection.");
  if (args.includes("state=closed") && has(/\/pulls\/\d+\/?$/)) deny("Refused: this gh api call closes a pull request, which is the same as gh pr close.");
};

const checkOdoo = (args) => {
  if (args.some((a) => /^--(addons-path|data-dir)(=|$)/.test(a))) {
    deny("Refused: tree pinning. --addons-path and --data-dir on an Odoo call outside install_all.sh." + PIN_ADVICE);
  }
  if (args.includes("upgrade")) deny("Refused: database upgrade. odoo upgrade is a human-run step.");
  if (!/odoo-dev-upgrade/.test(agentType) || args.find((a) => !a.startsWith("-")) === "upgrade_code") return;
  if (args.some((a) => ["-i", "-u", "--init", "--update"].includes(a) || /^--(init|update)=/.test(a))) {
    process.stderr.write("advisory: a direct odoo -i or -u install. The install loop script keeps its cap and summary, so prefer it.\n");
  }
};

// The remote command of an ssh call is what follows the host. Only three forms pass.
const checkSsh = (args) => {
  const pos = [];
  for (let i = 0; i < args.length; i += 1) {
    if (pos.length === 0 && args[i].startsWith("-")) { if (SSH_VALUE_OPTS.has(args[i])) i += 1; continue; }
    pos.push(args[i]);
  }
  const cmd = pos.slice(1).join(" ").trim();
  const readOnly = !/[;&`\n]|\$\(|\|\|/.test(cmd) && (cmd === "true" ||
    (/^psql\s+-Atc\s+["']?\s*select\b/i.test(cmd) && !WRITE_SQL.test(cmd)) ||
    base(cmd.split(/\s+/)[0]) === "studio_inventory.py");
  if (!readOnly) {
    deny("Refused: database upgrade and production. ssh passes only true, psql -Atc \"select ...\" and studio_inventory.py (got: " + (cmd || "an interactive shell") + ").");
  }
};

const checkWrite = () => {
  const fp = typeof toolInput.file_path === "string" && toolInput.file_path ? toolInput.file_path
    : (typeof toolInput.notebook_path === "string" ? toolInput.notebook_path : "");
  if (!fp) deny("Refused: write outside scope. This " + toolName + " call carried no file_path.");
  const e = expand(fp);
  if (e === null) deny("Refused: write outside scope. The path " + fp + " cannot be resolved.");
  const abs = path.resolve(PCWD, e);
  if (abs === STATE || abs.startsWith(STATE + path.sep) || inTree(abs, PCWD)) return;
  deny("Refused: write outside scope. " + abs + " is neither inside a git working tree nor under the odoo-dev state directory.");
};

const checkSimple = (toks, st, depth) => {
  for (let i = 0; i < toks.length; i += 1) {     // redirects: the next token is the target
    const n = toks[i + 1];
    if (toks[i].redir && n && !n.redir && !n.here) checkTarget([n.t], st.cwd, "a shell redirect into", "Edit or Write");
  }
  if (toks.bg) deny(BG_MSG);
  const here = toks.findIndex((x) => x.here);
  const hereText = here >= 0 && toks[here + 1] ? toks[here + 1].t : null;
  const words = toks.filter((x, i) => x.t !== undefined && !(i > 0 && (toks[i - 1].redir || toks[i - 1].here))).map((x) => x.t);
  const w = unwrap(words);
  if (w.detached || (w.length && base(w[0]) === "disown")) deny(BG_MSG);
  if (!w.length) return;
  const head = base(w[0]), args = w.slice(1);
  const sub = (text) => { if (depth < 4) analyse(text, st, depth + 1); };

  if (CONTAINERS.has(head)) deny("Refused: " + head + " in any form. The container is already the environment.");
  if (SHELLS.has(head) || head === "eval") {
    const ci = head === "eval" ? -1 : args.findIndex((a) => /^-[a-z]*c[a-z]*$/.test(a));
    if (head === "eval") { if (args.length) sub(args.join(" ")); }
    else if (ci >= 0 && args[ci + 1] !== undefined) sub(args[ci + 1]);
    else if (hereText !== null) sub(hereText);
    else if (toks.pipedFrom) {     // echo '...' | bash: the text of the producer is the script
      const src = toks.pipedFrom.filter((x) => x.t !== undefined).map((x) => x.t);
      if (src.length > 1) sub(src.slice(1).join(" "));
    }
    return;
  }
  if (head === "alias") { for (const a of args) { const k = a.indexOf("="); if (k > 0) sub(a.slice(k + 1)); } return; }
  if (head === "cd" || head === "pushd") {
    const t = args.find((a) => !a.startsWith("-"));
    const e = t !== undefined ? expand(t) : null;
    if (e !== null) st.cwd = path.resolve(st.cwd, e);
    return;
  }
  if (head === "find") {
    for (let i = 0; i < args.length; i += 1) {
      if (!["-exec", "-execdir", "-ok", "-okdir"].includes(args[i])) continue;
      let k = i + 1;
      while (k < args.length && args[k] !== ";" && args[k] !== "+") k += 1;
      checkSimple(args.slice(i + 1, k).map((t) => ({ t, q: false })), st, depth + 1);
    }
    return;
  }
  if (head === "ln" || head === "mv" || head === "cp") {
    const ops = args.filter((a) => !a.startsWith("-")), last = ops[ops.length - 1];
    if ((head !== "ln" || args.some((a) => /^-[A-Za-z]*s/.test(a) || a === "--symbolic")) && last !== undefined && pinsTree(last, st.cwd)) {
      deny("Refused: tree pinning. " + head + " into " + last + " changes which addons tree Odoo loads." + PIN_ADVICE);
    }
    return;
  }
  if (ODOO_HEAD.test(head)) return checkOdoo(args);
  if (/^upgrade_service(\.sh)?$/.test(head)) {
    if (args.includes("production")) deny("Refused: database upgrade. upgrade_service.sh production is a human-run step.");
    return;
  }
  if (head === "ssh") return checkSsh(args);
  if (head === "git") return checkGit(args, st);
  if (head === "gh") return checkGh(args);
  if (/^gh-as-owner(\.sh)?$/.test(head)) {
    if (/^pr-(merge|ready|review)$/.test(args[0] || "")) deny("Refused: gh-as-owner " + args[0] + " is never allowed unattended.");
    if (args[0] === "gh") checkGh(args.slice(1));
    if (args[0] === "push") {     // push <worktree> <branch> [flags]
      const rest = args.slice(1), plain = rest.filter((a) => !a.startsWith("-"));
      checkPush(["origin", ...rest.filter((a) => a.startsWith("-")), ...plain.slice(1)], path.resolve(st.cwd, plain[0] || st.cwd));
    }
    return;
  }
  if (head === "sed" || head === "perl") {
    if (!args.some((a) => a === "--in-place" || a.startsWith("--in-place=") || /^-[A-Za-z]*i/.test(a))) return;
    let files = args.filter((a) => !a.startsWith("-"));
    if (!args.some((a) => a === "-e" || a === "-f" || a.startsWith("--expression") || a.startsWith("--file"))) files = files.slice(1);
    checkTarget(files, st.cwd, head + " -i edits", "Edit");
    return;
  }
  if (head === "tee") checkTarget(args.filter((a) => !a.startsWith("-")), st.cwd, "tee writes", "Write");
};

function analyse(cmd, st, depth) {
  // A backslash-newline joins two lines, but only when the run of backslashes is odd.
  const { text, bodies } = extractHeredocs(cmd.replace(/(?<!\\)((?:\\\\)*)\\\r?\n/g, "$1"));
  for (const seg of parse(text)) checkSimple(seg, st, depth);
  if (depth < 4) {
    for (const inner of nested(text)) analyse(inner, { cwd: st.cwd }, depth + 1);
    for (const b of bodies) analyse(b, { cwd: st.cwd }, depth + 1);
  }
}

// A STOP file for this job kills the run; read-only tools and upgrade-state.sh still pass.
const READ_ONLY = ["Read", "Glob", "Grep", "LS", "WebFetch", "WebSearch", "TodoWrite", "Task"];
const stopFile = () => {
  const job = process.env.ODOO_JOB_ID;
  if (!job) return "";
  const tasks = path.join(STATE, "tasks");
  try {
    for (const d of fs.readdirSync(tasks)) {
      const f = path.join(tasks, d, "jobs", job, "STOP");
      if (fs.existsSync(f)) return f;
    }
  } catch { /* no tasks directory */ }
  return "";
};

try {
  if (payload === null) {
    if (RUN || raw.includes(PREFIX)) deny("The unattended guard could not parse this hook payload, so it denied rather than guessing.");
    process.exit(0);
  }
  if (!(RUN || agentType.startsWith(PREFIX) || agentType.includes(":" + PREFIX))) process.exit(0);
  if (!READ_ONLY.includes(toolName)) {
    const stop = stopFile();
    const c = command || "";
    const stateCall = toolName === "Bash" && /^\s*(?:bash\s+)?\S*upgrade-state\.sh(?:\s|$)/.test(c) && !/[;&|<>`$()\n]/.test(c);
    if (stop && !stateCall) deny("Refused: a STOP file exists at " + stop + "; the run is killed. Only upgrade-state.sh may run.");
  }
  if (["Edit", "Write", "NotebookEdit"].includes(toolName)) { checkWrite(); process.exit(0); }
  if (toolName !== "Bash") process.exit(0);
  if (toolInput.run_in_background) deny(BG_MSG);
  if (command === null) deny("This Bash call carried no tool_input.command, so the unattended guard cannot tell what would run.");
  analyse(command, { cwd: PCWD }, 0);
} catch (e) {
  deny("The unattended guard failed while checking this command (" + (e && e.message ? e.message : String(e)) + "), so it denied rather than guessing.");
}
