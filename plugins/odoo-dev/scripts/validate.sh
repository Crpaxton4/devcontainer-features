#!/usr/bin/env bash
# validate.sh — every CI gate for the odoo-dev plugin, in one call.
#
# Called by the post-rebuild checklist and before any change to the plugin is
# trusted. Offline: no network, no docker, no Odoo, no repos tree.
#
# Runs ALL gates rather than stopping at the first, because the useful answer is
# "these three things are wrong".
#
# Usage: validate.sh [--quiet]
# Env:   REQUIRE_CLAUDE=1  fail, rather than skip, when the claude CLI is absent.
#                          The CI workflow sets it, so a green run there can never
#                          be one where the manifest gate quietly did not happen.
# Exit codes: 0 every gate that ran passed | 1 at least one gate failed
#
# Every gate but the first runs with nothing but bash, coreutils, git and node. The
# first shells out to `claude plugin validate`, which is the one tool this repo does
# not ship; see the gate itself for what happens when it is missing.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
SKILLS="$ROOT/skills"
AGENTS="$ROOT/agents"
COMMANDS="$ROOT/commands"
QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1

fails=0
skips=0
gate()   { printf '\n=== %s\n' "$*"; }
ok()     { echo "  PASS $*"; }
bad()    { echo "  FAIL $*"; fails=$((fails + 1)); }
# A skipped gate is a gate that did NOT run. It is counted separately from a pass
# and named again in the verdict, because a run that quietly checks less than it
# claims is the failure this whole suite exists to prevent.
skip()   { echo "  SKIP $*"; skips=$((skips + 1)); }
detail() { [ "$QUIET" -eq 1 ] || echo "       $*"; }

# --- 1. manifest and components ------------------------------------------------
# The only gate that needs a tool this repo does not ship. `claude plugin validate`
# reads the manifest off disk: measured against Claude Code 2.1.247 it runs to a
# clean pass with an empty HOME, needs no account, and writes nothing, so CI can
# install the CLI and run this gate for real rather than working around it.
#
# When the CLI is genuinely absent the gate is SKIPPED, not failed and not passed —
# `command not found` used to be reported as a FAIL, which made every run on a
# machine without the CLI look like a broken manifest. A skip is counted on its own
# and repeated in the verdict. REQUIRE_CLAUDE=1 turns the skip back into a failure,
# which is what CI sets: there the CLI is installed a step earlier, so its absence
# means the install broke and the run must go red rather than green-with-a-skip.
gate "manifest + components (claude plugin validate --strict)"
if ! command -v claude >/dev/null 2>&1; then
  if [ "${REQUIRE_CLAUDE:-0}" = "1" ]; then
    bad "claude CLI is not on PATH, and REQUIRE_CLAUDE=1 forbids skipping this gate"
    echo "       install it with: npm install -g @anthropic-ai/claude-code"
  else
    skip "claude CLI is not on PATH — the plugin manifest was NOT validated"
    detail "install it with: npm install -g @anthropic-ai/claude-code"
    detail "no account is needed: plugin validate only reads the manifest off disk"
  fi
elif out="$(claude plugin validate "$ROOT" --strict 2>&1)"; then
  ok "validation passed"
else
  bad "claude plugin validate --strict"
  echo "$out" | sed 's/^/       /'
fi

# --- 2. no SKILL.md at the plugin root -----------------------------------------
# A SKILL.md here triggers the single-skill-plugin path and collapses every
# bundled skill into one. It is the single most destructive mistake possible in
# this tree.
gate "no SKILL.md at plugin root"
if [ -e "$ROOT/SKILL.md" ]; then
  bad "$ROOT/SKILL.md exists — this collapses every bundled skill into one"
else
  ok "plugin root carries no SKILL.md"
fi

# --- 3. inventory ---------------------------------------------------------------
gate "inventory"
n_skills="$(find "$SKILLS" -mindepth 2 -maxdepth 2 -name SKILL.md | wc -l)"
n_agents="$(find "$AGENTS" -maxdepth 1 -name '*.md' | wc -l)"
n_cmds="$(find "$COMMANDS" -maxdepth 1 -name '*.md' 2>/dev/null | wc -l)"
[ "$n_skills" -eq 15 ] && ok "15 skills"   || bad "expected 15 skills, found $n_skills"
[ "$n_agents" -eq 5 ]  && ok "5 agents"    || bad "expected 5 agents, found $n_agents"
[ "$n_cmds"   -eq 5 ]  && ok "5 commands"  || bad "expected 5 commands, found $n_cmds"

# A plugin command and a plugin skill share one namespace: both register as
# `odoo-dev:<stem>`, and the loader keeps ONE entry per name. So commands/pr.md
# would not sit alongside skills/odoo-pr/ — it would shadow a skill named odoo-pr
# outright, and every agent that preloads that skill by name would silently get the
# command body instead. `claude plugin validate --strict` does not catch this.
clash=0
for c in $(find "$COMMANDS" -maxdepth 1 -name '*.md' -printf '%f\n' 2>/dev/null | sed 's/\.md$//' | sort); do
  [ -f "$SKILLS/$c/SKILL.md" ] && { bad "/odoo-dev:$c collides with the odoo-dev:$c skill — one shadows the other"; clash=1; }
done
[ "$clash" -eq 0 ] && ok "no command name shadows a skill name"

# Every skill dir must be exactly <skills>/<name>/SKILL.md — one level deeper and
# it does not load; one level shallower and the personal-skill scan picks it up too.
stray="$(find "$SKILLS" -mindepth 3 -name SKILL.md)"
[ -z "$stray" ] && ok "no SKILL.md nested too deep" \
  || { bad "SKILL.md nested below <skills>/<name>/"; echo "$stray" | sed 's/^/       /'; }

# --- 4. frontmatter ------------------------------------------------------------
gate "skill frontmatter"
node -e '
const fs = require("fs"), path = require("path");
const skills = process.argv[1];
let bad = 0;
for (const d of fs.readdirSync(skills).sort()) {
  const f = path.join(skills, d, "SKILL.md");
  if (!fs.existsSync(f)) continue;
  const text = fs.readFileSync(f, "utf8");
  const m = text.match(/^---\n([\s\S]*?)\n---\n/);
  if (!m) { console.log(`  FAIL ${d}: no frontmatter`); bad++; continue; }
  const fm = m[1];
  const name = (fm.match(/^name:\s*(.+)$/m) || [])[1];
  if (!name) { console.log(`  FAIL ${d}: no name`); bad++; }
  else if (name.trim().replace(/^["\x27]|["\x27]$/g, "") !== d) {
    console.log(`  FAIL ${d}: name "${name.trim()}" does not match directory`); bad++;
  }
  if (/^name:\s*["\x27]?odoo-dev:/m.test(fm)) {
    console.log(`  FAIL ${d}: frontmatter name is namespaced; keep it bare`); bad++;
  }
  // description may be plain, quoted, or a >/| block; take everything up to the
  // next top-level key.
  // $ under /m is end-of-LINE, so the lazy match used to stop at the first line
  // and a multi-line block scalar measured as ~2 chars — the cap was unenforceable
  // for exactly the values most likely to overrun it. $(?![\s\S]) is end-of-STRING.
  const dm = fm.match(/^description:\s*([\s\S]*?)(?=\n[a-zA-Z_-]+:|$(?![\s\S]))/m);
  if (!dm) { console.log(`  FAIL ${d}: no description`); bad++; continue; }
  const desc = dm[1].replace(/^[>|][-+]?\n/, "").replace(/\s+/g, " ").trim()
                    .replace(/^["\x27]|["\x27]$/g, "");
  const when = (fm.match(/^when_to_use:\s*([\s\S]*?)(?=\n[a-zA-Z_-]+:|$(?![\s\S]))/m) || ["",""])[1]
                 .replace(/\s+/g, " ").trim();
  if (desc.length > 1024) { console.log(`  FAIL ${d}: description ${desc.length} chars > 1024`); bad++; }
  if (desc.length + when.length > 1536) {
    console.log(`  FAIL ${d}: description + when_to_use ${desc.length + when.length} chars > 1536`); bad++;
  }
}
if (bad === 0) console.log("  PASS every skill: name matches dir, description within limits");
process.exit(bad === 0 ? 0 : 1);
' "$SKILLS" || fails=$((fails + 1))

# --- 5. body size ---------------------------------------------------------------
# A regression guard, not a target: current bodies run 47-249 lines.
gate "SKILL.md body size (<= 500 lines)"
over=0
while IFS= read -r f; do
  n="$(wc -l < "$f")"
  if [ "$n" -gt 500 ]; then bad "$(basename "$(dirname "$f")"): $n lines"; over=1; fi
done < <(find "$SKILLS" -mindepth 2 -maxdepth 2 -name SKILL.md | sort)
[ "$over" -eq 0 ] && ok "every body <= 500 lines"

# --- 6. router completeness -----------------------------------------------------
gate "router completeness"
ROUTER="$SKILLS/odoo-dev-map/SKILL.md"
if [ ! -f "$ROUTER" ]; then
  bad "router skill missing at $ROUTER"
else
  missing=0
  for d in $(find "$SKILLS" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort); do
    grep -q "odoo-dev:$d\`" "$ROUTER" || { bad "router does not reference odoo-dev:$d"; missing=1; }
  done
  [ "$missing" -eq 0 ] && ok "every skill appears in the router Skill Map"

  amiss=0
  for a in $(find "$AGENTS" -maxdepth 1 -name '*.md' -printf '%f\n' | sed 's/\.md$//' | sort); do
    grep -q "$a" "$ROUTER" || { bad "router does not reference agent $a"; amiss=1; }
  done
  [ "$amiss" -eq 0 ] && ok "every agent appears in the router Agent Map"
fi

# --- 7. agent definitions -------------------------------------------------------
gate "agent definitions"
node -e '
const fs = require("fs"), path = require("path");
const [agentsDir, skillsDir] = process.argv.slice(1);
const known = new Set(fs.readdirSync(skillsDir).filter(d =>
  fs.existsSync(path.join(skillsDir, d, "SKILL.md"))));
let bad = 0;
for (const f of fs.readdirSync(agentsDir).filter(f => f.endsWith(".md")).sort()) {
  const text = fs.readFileSync(path.join(agentsDir, f), "utf8");
  const m = text.match(/^---\n([\s\S]*?)\n---\n/);
  if (!m) { console.log(`  FAIL ${f}: no frontmatter`); bad++; continue; }
  const fm = m[1];
  const name = (fm.match(/^name:\s*(.+)$/m) || [])[1]?.trim();
  if (name !== f.replace(/\.md$/, "")) { console.log(`  FAIL ${f}: name "${name}" != filename`); bad++; }
  // Plugin agent names are NOT auto-namespaced, so they would collide across
  // plugins without the prefix.
  if (!name?.startsWith("odoo-dev-")) { console.log(`  FAIL ${f}: name must start with odoo-dev-`); bad++; }
  if (!/^description:/m.test(fm)) { console.log(`  FAIL ${f}: no description`); bad++; }
  // A skills: entry that names nothing is skipped with only a debug-log warning.
  const sm = fm.match(/^skills:\n((?:\s+-\s+.+\n)+)/m);
  if (!sm) { console.log(`  FAIL ${f}: no skills: list`); bad++; }
  else for (const line of sm[1].trim().split("\n")) {
    const s = line.replace(/^\s*-\s*/, "").trim();
    if (s.startsWith("odoo-dev:")) {
      console.log(`  WARN ${f}: skills: entry "${s}" is namespaced; ship bare names`);
    } else if (!known.has(s)) {
      console.log(`  FAIL ${f}: skills: names "${s}", which is not a bundled skill`); bad++;
    }
  }
  // Unsupported for plugin agents, and silently ignored rather than rejected.
  for (const k of ["hooks", "mcpServers"]) {
    if (new RegExp(`^${k}:`, "m").test(fm)) { console.log(`  FAIL ${f}: ${k}: is not supported for plugin agents`); bad++; }
  }
  if (/^isolation:\s*worktree/m.test(fm)) {
    console.log(`  FAIL ${f}: isolation: worktree — worktree-ensure.sh owns the worktree, and the stack addons path points at that one`); bad++;
  }
}
if (bad === 0) console.log("  PASS every agent: named odoo-dev-*, skills resolve, no unsupported keys");
process.exit(bad === 0 ? 0 : 1);
' "$AGENTS" "$SKILLS" || fails=$((fails + 1))

# --- 8. namespacing --------------------------------------------------------------
gate "sibling references are namespaced"
node -e '
const fs = require("fs"), path = require("path");
const skillsDir = process.argv[1];
const names = fs.readdirSync(skillsDir).filter(d =>
  fs.existsSync(path.join(skillsDir, d, "SKILL.md")));
const pat = new RegExp("`(" + names.slice().sort((a, b) => b.length - a.length).join("|") + ")`", "g");
let bad = 0;
const walk = d => fs.readdirSync(d, { withFileTypes: true }).flatMap(e =>
  e.isDirectory() ? walk(path.join(d, e.name)) : [path.join(d, e.name)]);
for (const f of walk(skillsDir).filter(f => f.endsWith(".md")).sort()) {
  const own = path.relative(skillsDir, f).split(path.sep)[0];
  let text = fs.readFileSync(f, "utf8");
  const m = text.match(/^---\n[\s\S]*?\n---\n/);
  if (m) text = text.slice(m[0].length);
  text.split("\n").forEach((line, i) => {
    for (const hit of line.matchAll(pat)) {
      if (hit[1] === own) continue;
      console.log(`  FAIL ${path.relative(skillsDir, f)}:${i + 1}: bare \`${hit[1]}\` in invocation position`);
      bad++;
    }
  });
}
if (bad === 0) console.log("  PASS no bare sibling-skill references");
process.exit(bad === 0 ? 0 : 1);
' "$SKILLS" || fails=$((fails + 1))

# --- 9. no absolute in-tree paths ------------------------------------------------
gate "no hard-coded plugin paths"
hits="$(grep -rn "/usr/local/share/claude-home/skills" --include='*.md' "$SKILLS" \
        | grep -v 'CLAUDE_PLUGIN_ROOT:-' || true)"
[ -z "$hits" ] && ok "only self-resolving fallbacks remain" \
  || { bad "hard-coded paths"; echo "$hits" | sed 's/^/       /'; }

# --- 10. stray feature-managed skills --------------------------------------------
gate "stray feature-managed skills"
strays="$(bash "$HERE/check-stray-skills.sh" 2>/dev/null)"
[ -z "$strays" ] && ok "none loaded one level deep" \
  || { bad "leftover pre-migration loose skill copies (safe to delete, see check-stray-skills.sh)"; echo "$strays" | sed 's/^/       /'; }

# --- 11. script regression + gate unit tests -------------------------------------
gate "offline test suites"
run_suite() {
  local label="$1" script="$2"
  if [ ! -f "$script" ]; then bad "$label: missing ($script)"; return; fi
  if out="$(bash "$script" 2>&1)"; then ok "$label"; else
    bad "$label"; echo "$out" | tail -15 | sed 's/^/       /'
  fi
}
run_suite "gate.test.sh"           "$HERE/tests/gate.test.sh"
run_suite "setup.test.sh"          "$HERE/tests/setup.test.sh"
run_suite "hooks.test.sh"          "$HERE/tests/hooks.test.sh"
run_suite "module-classify.test.sh" "$HERE/tests/module-classify.test.sh"
run_suite "repo-map.test.sh"       "$SKILLS/odoo-repo-map/scripts/tests/repo-map.test.sh"
run_suite "existing-work.test.sh"  "$SKILLS/odoo-task-env/scripts/tests/existing-work.test.sh"
run_suite "task-env.test.sh"       "$SKILLS/odoo-task-env/scripts/tests/task-env.test.sh"
run_suite "run-tests.test.sh"      "$SKILLS/odoo-test-run/scripts/tests/run-tests.test.sh"
run_suite "pr-open.test.sh"        "$SKILLS/odoo-pr/scripts/tests/pr-open.test.sh"
run_suite "release-manifest.test.sh" "$SKILLS/odoo-release/scripts/tests/release-manifest.test.sh"

# --- 12. eval suite --------------------------------------------------------------
# `claude plugin eval` is early-access gated, so the suite cannot be RUN here. The
# cases are still checked structurally, because a case file that does not parse is
# a gate that silently never runs the day early access lands.
gate "trigger-accuracy eval suite"
node -e '
const fs = require("fs"), path = require("path");
const E = process.argv[1];
if (!fs.existsSync(E)) { console.log("  FAIL evals/ missing"); process.exit(1); }
let bad = 0, pos = 0, neg = 0, train = 0, val = 0;
const skills = new Set(fs.readdirSync(path.join(E, "..", "skills")));
for (const d of fs.readdirSync(E).sort()) {
  const f = path.join(E, d, "case.yaml");
  if (!fs.existsSync(f)) continue;
  const t = fs.readFileSync(f, "utf8");
  for (const k of ["schema_version:", "name:", "tags:", "plugins:", "execution:", "graders:", "prompt:"])
    if (!t.includes(k)) { console.log(`  FAIL ${d}: missing ${k}`); bad++; }
  if (!new RegExp(`^name: ${d}$`, "m").test(t)) { console.log(`  FAIL ${d}: name does not match directory`); bad++; }
  if (!/^plugins: \[odoo-dev\]$/m.test(t)) { console.log(`  FAIL ${d}: plugins must be [odoo-dev]`); bad++; }
  const isPos = /positive/.test(t), isNeg = /negative/.test(t);
  if (isPos === isNeg) { console.log(`  FAIL ${d}: must be tagged positive or negative, not both or neither`); bad++; }
  if (isPos) {
    pos++;
    const m = t.match(/input_match: "(.+)"/);
    if (!m) { console.log(`  FAIL ${d}: no input_match`); bad++; }
    else if (!skills.has(m[1])) { console.log(`  FAIL ${d}: asserts skill "${m[1]}", which is not bundled`); bad++; }
    if (!/min: 1/.test(t)) { console.log(`  FAIL ${d}: positive case must assert min: 1`); bad++; }
  }
  if (isNeg) {
    neg++;
    if (!/max: 0/.test(t)) { console.log(`  FAIL ${d}: near-miss case must assert max: 0`); bad++; }
  }
  if (/(\[|, )train\]/.test(t)) train++;
  if (/validation\]/.test(t)) val++;
}
if (pos < 10) { console.log(`  FAIL only ${pos} should-trigger cases, want >= 10`); bad++; }
if (neg < 10) { console.log(`  FAIL only ${neg} near-miss cases, want >= 10`); bad++; }
if (train === 0 || val === 0) { console.log("  FAIL no train/validation split"); bad++; }
if (bad === 0) console.log(`  PASS ${pos} should-trigger, ${neg} near-miss, split ${train} train / ${val} validation`);
process.exit(bad === 0 ? 0 : 1);
' "$ROOT/evals" || fails=$((fails + 1))

# The runner is NOT probed here. `claude plugin eval init` scaffolds a case
# directory when early access IS enabled, and a validator must not write to the
# tree it is validating.
detail "run the suite with: claude plugin eval odoo-dev@devcontainer-features --ablation with-without"
detail "(gated by early access on this account as of 2026-09-07)"

# --- 13. shell syntax -------------------------------------------------------------
gate "shell syntax"
synbad=0
while IFS= read -r f; do
  bash -n "$f" 2>/dev/null || { bad "syntax: ${f#$ROOT/}"; synbad=1; }
done < <(find "$ROOT" -name '*.sh' -type f | sort)
[ "$synbad" -eq 0 ] && ok "every .sh parses"

# --- 14. no bare artifact.sh / gate.sh invocation ---------------------------------
# Every artifact write in the chain was once `command not found`: the bodies said
# `artifact.sh put ...` and nothing puts scripts/ on PATH. An invocation is a line
# whose FIRST non-whitespace token is the bare script name. A backticked prose
# mention (`artifact.sh` never overwrites) is a reference, not an invocation, and
# is deliberately left alone.
gate "no bare artifact.sh / gate.sh invocation"
bare="$(grep -nE '^[[:space:]]*(artifact|gate)\.sh([[:space:]]|$)' \
        "$AGENTS"/*.md "$SKILLS/odoo-dev-map/SKILL.md" 2>/dev/null || true)"
if [ -z "$bare" ]; then
  ok "agents + router invoke artifact.sh and gate.sh only through a resolved path"
else
  bad "bare script invocation — nothing puts scripts/ on PATH, so this is command not found"
  echo "$bare" | sed "s|^$ROOT/||" | sed 's/^/       /'
fi

# --- 15. no shell variable in command position in an agent body -------------------
# The sequel to gate 14, and the same bug wearing a different hat. Rewriting bare
# `artifact.sh put ...` into `"$ARTIFACT" put "$ARTIFACTS" ...` fixed nothing: a
# subagent's Bash call inherits no environment from the dispatcher (CLAUDE_PLUGIN_ROOT
# is substituted in plugin HOOK commands, not in a Bash tool call) and keeps no state
# from the previous call, so both variables are empty and the command runs as
# `put out.json`. Measured on 2026-09-07 against Claude Code 2.1.247.
gate "no shell variable in command position (agents)"
node -e '
const fs = require("fs"), path = require("path");
const agentsDir = process.argv[1];

// The handoff paths, which are the ones that actually get run. A body may name them
// in prose without the sigil ("unless the machine sets ODOO_DEV_STATE_DIR"); what it
// may not do is write one as something a shell has to expand.
const HANDOFF = /\$\{?(ARTIFACT|ARTIFACTS|GATE|CLAUDE_PLUGIN_ROOT|ODOO_DEV_STATE_DIR)\b/;

const teach = () => {
  console.log("       A subagent Bash call inherits no environment from the dispatcher and keeps");
  console.log("       no state from the call before it, so a variable in a prompt is not a path:");
  console.log("       \"$ARTIFACT\" put \"$ARTIFACTS\" 30-test out.json runs as put out.json.");
  console.log("       Write the absolute path the prompt handed you, in full, in every call, or");
  console.log("       an angle-bracket placeholder such as <ARTIFACT path from your prompt>,");
  console.log("       which cannot be mistaken for something a shell expands.");
};

let bad = 0, agents = 0, cmdlines = 0;
for (const f of fs.readdirSync(agentsDir).filter(n => n.endsWith(".md")).sort()) {
  agents++;
  const text = fs.readFileSync(path.join(agentsDir, f), "utf8");
  let fenced = false;
  text.split("\n").forEach((line, i) => {
    const where = f + ":" + (i + 1);
    // Fence delimiters toggle; the Return contract lives inside one, so it is covered.
    if (/^\s*`{3,}/.test(line.trim())) { fenced = !fenced; return; }
    if (fenced && line.trim()) {
      cmdlines++;
      const first = line.trim().split(/\s+/)[0].replace(/^["\x27]+/, "").replace(/["\x27]+$/, "");
      if (/^\$\{?[A-Za-z_]/.test(first)) {
        console.log("  FAIL " + where + ": this command starts with the shell variable " + first);
        bad++;
      }
    }
    if (HANDOFF.test(line)) {
      console.log("  FAIL " + where + ": " + line.trim().slice(0, 78));
      console.log("       names a handoff path as a shell variable");
      bad++;
    }
  });
}
if (bad) teach();
else console.log("  PASS " + agents + " agents, " + cmdlines + " fenced command lines, no shell variable in command position");
process.exit(bad === 0 ? 0 : 1);
' "$AGENTS" || fails=$((fails + 1))

# --- 16. no shell variable name means two different things ------------------------
gate "no duplicate shell variable across preloadable skills"
node -e '
const fs = require("fs"), path = require("path");
const skillsDir = process.argv[1];
// COVERS: ALL_CAPS NAME=VALUE bindings whose value is a path (contains / or ${})
// anywhere in a SKILL.md — the preloadable surface. references/ is never preloaded,
// so a name there cannot be redefined out from under a sibling in the same context.
// DOES NOT COVER: lowercase names (a Python kwarg and an XML attribute are spelled
// exactly like a shell assignment in the examples), names built across two lines,
// or names a called script exports rather than the SKILL.md itself.
// Same name + same value in two skills is deliberate borrowing, not a collision:
// odoo-release points MAP_SCRIPTS at odoo-repo-map scripts on purpose. Only a name
// bound to DIFFERENT values fails — that is what made SCRIPTS mean three things and
// let one preload redefine another out from under it.
const seen = new Map();
for (const d of fs.readdirSync(skillsDir).sort()) {
  const f = path.join(skillsDir, d, "SKILL.md");
  if (!fs.existsSync(f)) continue;
  fs.readFileSync(f, "utf8").split("\n").forEach((line, i) => {
    for (const m of line.matchAll(/(?:^|[^A-Za-z0-9_${])([A-Z][A-Z0-9_]{2,})=("[^"]*"|[^\s`;|)]*)/g)) {
      const val = m[2].replace(/^"|"$/g, "");
      if (!/\/|\$\{/.test(val)) continue;
      if (!seen.has(m[1])) seen.set(m[1], new Map());
      const byVal = seen.get(m[1]);
      if (!byVal.has(val)) byVal.set(val, []);
      byVal.get(val).push(`${d}/SKILL.md:${i + 1}`);
    }
  });
}
let bad = 0, shared = 0;
for (const [name, byVal] of [...seen.entries()].sort()) {
  const skills = new Set([...byVal.values()].flat().map(s => s.split("/")[0]));
  if (skills.size < 2) continue;
  if (byVal.size === 1) { shared++; continue; }
  console.log(`  FAIL ${name} is bound to ${byVal.size} different values across skills`);
  for (const [val, where] of byVal) console.log(`       ${where.join(", ")}  ${name}=${val}`);
  bad++;
}
if (bad === 0) console.log(`  PASS ${seen.size} path variables, no name means two different things (${shared} shared name(s), same value)`);
process.exit(bad === 0 ? 0 : 1);
' "$SKILLS" || fails=$((fails + 1))

# --- 17. every backticked field token resolves to an artifact.sh SCHEMA field -----
gate "agent field tokens exist in artifact.sh SCHEMA"
node -e '
const fs = require("fs"), path = require("path");
const [root, agentsDir] = process.argv.slice(1);

// The SCHEMA is a JS object literal held in a shell variable in scripts/artifact.sh.
// Chosen approach: lift the literal out whole and evaluate it, rather than re-parse
// the required:/types:/elements: arrays out of the file text. Re-parsing would drift
// from what artifact.sh actually enforces; evaluating reads the exact same object.
const src = fs.readFileSync(path.join(root, "scripts", "artifact.sh"), "utf8");
const lit = src.match(/\nSCHEMA=\x27\n([\s\S]*?)\n\x27\n/);
if (!lit) { console.log("  FAIL cannot lift the SCHEMA literal out of scripts/artifact.sh"); process.exit(1); }
const SCHEMA = new Function(lit[1] + "\nreturn SCHEMA;")();

const fieldsOf = stage => {
  const s = SCHEMA[stage]; if (!s) return [];
  const out = new Set(s.required ?? []);
  for (const k of Object.keys(s.types ?? {})) out.add(k);
  for (const [f, spec] of Object.entries(s.elements ?? {})) {
    out.add(f);
    for (const k of Object.keys(spec)) out.add(k);
  }
  return [...out];
};
const arraysOf = stage => Object.entries(SCHEMA[stage]?.types ?? {})
  .filter(([, t]) => t === "array").map(([k]) => k);

// Vocabulary a script emits but no stage requires — worktree_drift is a gate.sh
// block reason, log_excerpt is a run-tests.sh output field. A body may name these.
// The phantom this gate exists to catch (reasons[]) was in no schema AND emitted by
// no script, so widening to script keys does not let it through.
const scriptKeys = new Set();
const walk = d => fs.readdirSync(d, { withFileTypes: true }).flatMap(e =>
  e.name.startsWith(".") ? [] :
  e.isDirectory() ? walk(path.join(d, e.name)) : [path.join(d, e.name)]);
for (const f of walk(root).filter(f => f.endsWith(".sh"))) {
  const t = fs.readFileSync(f, "utf8");
  for (const m of t.matchAll(/["\x27]([a-z][a-z0-9]*(?:_[a-z0-9]+)+)["\x27]/g)) scriptKeys.add(m[1]);
  for (const m of t.matchAll(/([a-z][a-z0-9]*(?:_[a-z0-9]+)+):/g)) scriptKeys.add(m[1]);
}

// Neither a schema field nor script output, and still legitimate:
//   design_doc_path — a real but OPTIONAL 05-scope field. SCHEMA enumerates required
//                     fields only, so optional ones have to be named here until it
//                     grows an optional: list.
//   upgrade_code    — the odoo-bin subcommand. A tool name that happens to be snake_case.
const ALLOW = new Set(["design_doc_path", "upgrade_code"]);

let bad = 0, checked = 0, agents = 0;
for (const f of fs.readdirSync(agentsDir).filter(f => f.endsWith(".md")).sort()) {
  agents++;
  let text = fs.readFileSync(path.join(agentsDir, f), "utf8");
  const fm = text.match(/^---\n[\s\S]*?\n---\n/);
  if (fm) text = text.slice(fm[0].length);
  // Stages the agent writes come from its own Return contract; stages it reads are
  // named as `NN-stage.json` in the body.
  // `put` then the stage, with the artifacts dir between them in whatever form the
  // body uses — an absolute path, or a <ARTIFACTS dir from your prompt> placeholder.
  const writes = [...new Set([...text.matchAll(/\bput\b[^\n]*?(\d\d-[a-z]+)/g)].map(m => m[1]))];
  const reads  = [...text.matchAll(/`(\d\d-[a-z]+)\.json`/g)].map(m => m[1]);
  let touched = [...new Set([...writes, ...reads])].filter(s => s in SCHEMA);
  // An agent that names no stage at all (odoo-dev-pr reads the whole directory) is
  // checked against the union of every stage rather than against nothing.
  if (!touched.length) touched = Object.keys(SCHEMA);
  const vocab  = new Set(touched.flatMap(fieldsOf));
  const arrays = new Set(touched.flatMap(arraysOf));
  const wrote  = writes.length ? writes.join(", ") : "no stage";

  for (const m of text.matchAll(/`([^`\n]+)`/g)) {
    const tok = m[1];
    // A field token is a backticked token ending in [] or a bare snake_case name.
    // Filenames (a dot), stage names (NN-name), shell variables ($ or ALL_CAPS) and
    // anything with a slash or a space are excluded by these two shapes.
    if (/\[\]$/.test(tok)) {
      const base = tok.slice(0, -2);
      if (!/^[a-z][a-z0-9_]*$/.test(base)) continue;
      checked++;
      if (!arrays.has(base)) {
        console.log(`  FAIL ${f}: \`${tok}\` is an array field of no stage this agent touches`);
        console.log(`       touches ${touched.join(", ")} / writes ${wrote} — add it to the SCHEMA or drop it from the body`);
        bad++;
      }
    } else if (/^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$/.test(tok)) {
      checked++;
      if (!vocab.has(tok) && !scriptKeys.has(tok) && !ALLOW.has(tok)) {
        console.log(`  FAIL ${f}: \`${tok}\` is in no artifact.sh SCHEMA stage this agent touches, and no script emits it`);
        console.log(`       touches ${touched.join(", ")} / writes ${wrote} — add it to the SCHEMA or drop it from the body`);
        bad++;
      }
    }
  }
}
if (bad === 0) console.log(`  PASS ${checked} field tokens across ${agents} agents all resolve to an artifact.sh SCHEMA field`);
process.exit(bad === 0 ? 0 : 1);
' "$ROOT" "$AGENTS" || fails=$((fails + 1))

# --- 18. command injections fail soft under hostile input -------------------------
# `/odoo-dev:pr Create a release PR from UAT to main` used to come back as a raw
# `ls: cannot access '.../tasks/Create'`, because the guard on the injection was
# `test -n "$task"` — which asks whether an argument was typed, not whether it is a
# task id. A non-zero exit inside `` !`…` `` aborts the whole command expansion, so
# no agent was ever dispatched and the shell's own error became the user's error
# message. The `mkdir -p` variants failed the other way: they exited 0 and silently
# created an artifacts directory named after a prose word.
#
# The rule enforced here is the one already documented for skill injections: every
# injection ends in a fail-soft tail and exits 0 whatever the argument holds. Named
# command arguments are substituted into the command TEXT before a shell ever sees
# it — that is why the failure above printed `test -n "Create"` — so this gate
# substitutes them the same way rather than exporting them, which makes the test
# strictly harsher than the runtime.
#
# Everything runs against a scratch ODOO_DEV_STATE_DIR and a scratch HOME, including
# one pass with ODOO_DEV_STATE_DIR unset so the `$HOME` default inside every
# injection is exercised too. Afterwards the scratch tree must hold no directory
# that a well-formed invocation could not have produced, which is how `tasks/Create`
# gets caught. The last pass then feeds real arguments in, because an injection that
# resolves nothing at all would otherwise pass every check above it.
gate "command injections fail soft under hostile input"
inj_root="$(mktemp -d)"
inj_list="$inj_root/injections.tsv"
node -e '
const fs = require("fs"), path = require("path");
const dir = process.argv[1];
for (const f of fs.readdirSync(dir).filter(n => n.endsWith(".md")).sort()) {
  fs.readFileSync(path.join(dir, f), "utf8").split("\n").forEach((line, i) => {
    for (const m of line.matchAll(/!`([^`]*)`/g)) console.log([f, i + 1, m[1]].join("\t"));
  });
}
' "$COMMANDS" > "$inj_list" 2>/dev/null

# Argument values that reach a real command line. The prose word is the one the bug
# was reported with; the traversal and the metacharacter string are what a path
# built out of an unvalidated token has to survive; `release` is the route sentinel,
# which must be inert everywhere it is not the route; `$(id)` is harmless in itself
# and is here to show that a command substitution is rejected rather than acted on.
inj_values=("" "Create" "../../etc" "a b; rm -rf * | true" "release" '$(id)')

# Render an injection the way the runtime does: textual substitution, no exporting.
inj_render() {
  local c="$1"
  c="${c//\$\{CLAUDE_PLUGIN_ROOT\}/$ROOT}"
  c="${c//\$from_branch/$3}"
  c="${c//\$to_branch/$4}"
  c="${c//\$task/$2}"
  printf '%s' "$c"
}

# Anything under a scratch state dir that a well-formed invocation could not have
# made. A task directory may only ever be named by a numeric id, and no value above
# is one, so ANY entry under tasks/ is junk; a release directory must be one plain
# `<from>-to-<to>` component directly under releases/.
inj_junk() {
  [ -d "$1" ] || return 0
  local p rel leaf
  while IFS= read -r p; do
    rel="${p#$1/}"
    case "$rel" in
      tasks|releases) continue ;;
      releases/*)
        leaf="${rel#releases/}"
        case "$leaf" in
          */*) echo "$rel" ;;
          *) echo "$leaf" | grep -qE '^[A-Za-z0-9][A-Za-z0-9._-]*-to-[A-Za-z0-9][A-Za-z0-9._-]*$' || echo "$rel" ;;
        esac ;;
      *) echo "$rel" ;;
    esac
  done < <(find "$1" -mindepth 1 2>/dev/null)
}

inj_n=0
inj_runs=0
inj_report=""
while IFS=$'\t' read -r cf cl craw; do
  [ -n "${cf:-}" ] || continue
  inj_n=$((inj_n + 1))
  for v in "${inj_values[@]}"; do
    for mode in same release nostate; do
      case "$mode" in
        release) cmd="$(inj_render "$craw" release "$v" "$v")" ;;
        *)       cmd="$(inj_render "$craw" "$v" "$v" "$v")" ;;
      esac
      if [ "$mode" = nostate ]; then
        out="$(env -u ODOO_DEV_STATE_DIR HOME="$inj_root/home" bash -c "$cmd" </dev/null 2>&1)"
      else
        out="$(env HOME="$inj_root/home" ODOO_DEV_STATE_DIR="$inj_root/state" \
               bash -c "$cmd" </dev/null 2>&1)"
      fi
      rc=$?
      inj_runs=$((inj_runs + 1))
      [ "$rc" -eq 0 ] && continue
      inj_report="$inj_report
  $cf:$cl exits $rc in $mode mode on the argument [$v]
       rendered: $(printf '%s' "$cmd" | tr '\n' ' ' | cut -c1-150)
       said:     $(printf '%s' "$out" | tr '\n' ' ' | cut -c1-150)"
    done
  done
done < "$inj_list"

inj_junk_found="$( { inj_junk "$inj_root/state"; inj_junk "$inj_root/home/.local/share/odoo-dev"; } | sort -u)"

# Real arguments, in their own scratch tree, so that deleting an injection or
# writing one that can only ever emit its marker cannot pass this gate.
mkdir -p "$inj_root/live/tasks/30412" "$inj_root/livehome"
while IFS=$'\t' read -r cf cl craw; do
  [ -n "${cf:-}" ] || continue
  cmd="$(inj_render "$craw" 30412 UAT main)"
  out="$(env HOME="$inj_root/livehome" ODOO_DEV_STATE_DIR="$inj_root/live" \
         bash -c "$cmd" </dev/null 2>&1)"
  rc=$?
  [ "$rc" -eq 0 ] || inj_report="$inj_report
  $cf:$cl exits $rc on a real task id
       said:     $(printf '%s' "$out" | tr '\n' ' ' | cut -c1-150)"
  printf '%s\n' "$out" >> "$inj_root/live-$cf.out"
  cmd="$(inj_render "$craw" release UAT main)"
  env HOME="$inj_root/livehome" ODOO_DEV_STATE_DIR="$inj_root/live" \
      bash -c "$cmd" </dev/null >> "$inj_root/live-$cf.out" 2>&1
done < "$inj_list"

inj_mute=""
for c in $(find "$COMMANDS" -maxdepth 1 -name '*.md' -printf '%f\n' 2>/dev/null | sort); do
  grep -q "$inj_root/live" "$inj_root/live-$c.out" 2>/dev/null || inj_mute="$inj_mute $c"
done
[ -d "$inj_root/live/releases/UAT-to-main" ] || inj_mute="$inj_mute (no releases/UAT-to-main)"

if [ "$inj_n" -eq 0 ]; then
  bad "no !\`…\` injection found in $COMMANDS — the extractor or the commands changed shape"
elif [ -n "$inj_report" ]; then
  bad "an injection exits non-zero, which aborts the whole command expansion"
  echo "$inj_report" | sed '/^$/d'
  detail "end every injection in a fail-soft tail: ... || echo 'MARKER', and say in the"
  detail "body what the agent must do when it reads that marker instead of a path."
elif [ -n "$inj_junk_found" ]; then
  bad "an injection created a directory no well-formed invocation could ask for"
  echo "$inj_junk_found" | sed 's/^/       /'
  detail "validate the task id before mkdir -p, never after: an unvalidated token"
  detail "becomes a junk artifacts directory named after a word of prose."
elif [ -n "$inj_mute" ]; then
  bad "these commands resolved nothing from real arguments:$inj_mute"
  detail "an injection that can only ever emit its marker is not fail-soft, it is dead."
else
  ok "$inj_n injections over ${#inj_values[@]} hostile values, $inj_runs runs, all exit 0"
  detail "scratch state dir left no junk directory, and real arguments still resolve"
fi
rm -rf "$inj_root"


# --- 19. release version does not drift -------------------------------------------
# `version` in .claude-plugin/plugin.json is the ONLY signal `claude plugin update`
# reacts to, and release-please owns it: it bumps that field through the extra-files
# entry in release-please-config.json, and records the same number in
# .release-please-manifest.json as the point it counts the next release from.
#
# Hand-edit one and not the other and the two stop agreeing. release-please then
# computes the next version from a number nothing else believes, and the release
# ships under a version an installed copy may already hold — so `plugin update`
# fetches nothing and the work is invisible, silently. No other gate reads either
# file, so this is the only thing holding them together.
gate "release version does not drift"
# The plugin lives at plugins/odoo-dev inside the devcontainer-features monorepo,
# whose root release-please manifest owns the version under the package key
# "plugins/odoo-dev". Paths and keys below point there; the extra-files path stays
# package-relative, so the target check is unchanged.
REPO_ROOT="$(cd "$ROOT/../.." && pwd)"
rp_manifest="$REPO_ROOT/.release-please-manifest.json"
rp_config="$REPO_ROOT/release-please-config.json"
# Each read prints the empty string rather than failing, so a malformed or missing
# field is reported by the comparison below instead of aborting the gate.
rp_read() {
  node -e '
    let v;
    try {
      const o = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"));
      v = process.argv[2] === "plugin"   ? o.version
        : process.argv[2] === "manifest" ? o["plugins/odoo-dev"]
        : (((o.packages || {})["plugins/odoo-dev"] || {})["extra-files"] || [])[0]?.path;
    } catch (e) { v = null; }
    process.stdout.write(v == null ? "" : String(v));
  ' "$1" "$2" 2>/dev/null
}
if [ ! -f "$rp_manifest" ] || [ ! -f "$rp_config" ]; then
  bad "release tooling is incomplete — release-please-config.json and .release-please-manifest.json must both exist"
  detail "without them nothing bumps plugin.json on merge, and no installed copy ever updates"
else
  ver_plugin="$(rp_read "$ROOT/.claude-plugin/plugin.json" plugin)"
  ver_manifest="$(rp_read "$rp_manifest" manifest)"
  rp_target="$(rp_read "$rp_config" target)"
  if [ -z "$ver_plugin" ] || [ -z "$ver_manifest" ]; then
    bad "no version to compare: plugin.json gave '$ver_plugin', .release-please-manifest.json gave '$ver_manifest'"
    detail "plugin.json needs a top-level \"version\"; the manifest needs the key \"plugins/odoo-dev\"."
  elif [ "$ver_plugin" != "$ver_manifest" ]; then
    bad "plugin.json says $ver_plugin, .release-please-manifest.json says $ver_manifest"
    detail "release-please writes both together. If you bumped one by hand, bump the other to"
    detail "match — or revert yours and let a merge to main cut the release instead."
  elif [ "$rp_target" != ".claude-plugin/plugin.json" ]; then
    bad "release-please-config.json bumps '$rp_target', not .claude-plugin/plugin.json"
    detail "that extra-files entry is what moves the one field an installed copy watches;"
    detail "point it back at .claude-plugin/plugin.json or a release ships nothing."
  else
    ok "plugin.json and .release-please-manifest.json both at $ver_plugin"
    detail "and release-please-config.json bumps .claude-plugin/plugin.json on release"
  fi
fi

printf '\n'
if [ "$fails" -gt 0 ]; then
  if [ "$skips" -gt 0 ]; then
    echo "validate.sh: $fails gate(s) failed, and $skips gate(s) did not run"
  else
    echo "validate.sh: $fails gate(s) failed"
  fi
  exit 1
fi
if [ "$skips" -gt 0 ]; then
  # Deliberately not "all gates passed": some of them never ran, and the verdict
  # has to say so on the one line a reader is guaranteed to see.
  echo "validate.sh: every gate that ran passed, but $skips did NOT run — this is not a full validation"
  exit 0
fi
echo "validate.sh: all gates passed"
exit 0
