#!/usr/bin/env bash
# repo-map.sh — the ONLY sanctioned way to read or edit repo-map.json.
# Guarantees: schema-validated content, one .bak of the prior version, distinct
# exit codes, and a write that lands by renaming a scratch file within $MAP's own
# directory — so no reader ever sees a half-written map, and a write rejected by
# validation leaves neither a live map it failed nor a scratch file behind.
# NOT guaranteed: mutual exclusion. Two mutating invocations that overlap each
# read the map, write their own scratch file, and the later rename wins, so one
# update is silently lost and the .bak holds only the loser. Serialise callers
# yourself; this script takes no lock.
#
# Usage:
#   repo-map.sh get "<project name>"
#   repo-map.sh list
#   repo-map.sh add "<project name>" <repo> [--default-branch B] [--odoo-version V]
#                                          [--branch-flow ":task,UAT,main"] [--flow-confirmed]
#                                          [--remote owner/repo] [--notes "..."] [--no-repo-check]
#                                          [--release-assignee LOGIN] [--release-reviewer LOGIN|none]
#                                          [--repo-path /abs/path/to/checkout]
#   repo-map.sh set "<project name>" [--default-branch B] [--odoo-version V]
#                                    [--remote owner/repo] [--notes "..."]
#                                    [--repo-path /abs/path] [--no-repo-check]
#   repo-map.sh set-flow "<project name>" ":task,UAT,main" [--flow-confirmed]
#   repo-map.sh set-release-owners "<project name>" <assignee> <reviewer|none>
#   repo-map.sh remove "<project name>"
#   repo-map.sh validate
#
# `set` merges into an existing entry: a field you do not name keeps its value.
# It exists because `add` only ever creates, so the alternative was `remove` then
# a full `add` — lossy, since every field left off the re-`add` is silently
# dropped (`notes` first), and non-atomic, since the `remove` commits its own
# write before the `add` runs and takes the only .bak of the original with it.
# Passing an empty value (`--notes ""`) deletes that key, which is the one thing
# a merge cannot otherwise say. branch_flow is deliberately not settable here:
# `set-flow` owns it, along with the flow_confirmed question that comes with it.
#
# branch_flow is the ordered environment chain, LAST element production. It
# exists because the free-text notes field could only ever be read by a human:
# "flow: dev -> UAT -> main" is obvious prose and unusable as data, so promotion
# decisions could not be computed. flow_confirmed records whether a human has
# actually vouched for the chain — a flow parsed out of old notes is a
# hypothesis, and a wrong last element points a release at production.
#
# The FIRST element is where task work starts from, and on most projects that is
# not a shared branch at all: a task branch is cut fresh per unit of work, so no
# branch by that description exists on the remote. Writing an ordinary name there
# — the old "dev" convention — claims a branch every consumer walking the chain
# will believe in and none can resolve. The first element may therefore be the
# reserved token :task, which stands for "whatever branch this one task is on".
# A colon is forbidden anywhere in a git ref name, so the token cannot collide
# with a branch anyone could create, and the chain can be read without guessing
# at index 0. Every LATER element must be a real branch: those are what work gets
# promoted INTO, so they have to exist to be targeted.
#
# The token is optional and never required. A project whose task work is based
# directly on a shared environment records that branch as the first element and
# no placeholder at all; the rules are only that the placeholder, where present,
# is the first element and appears once, and that default_branch is never the
# placeholder — a task PR needs a real branch to target.
#
# repo_path is an optional ABSOLUTE path to this project's checkout, overriding
# the default $REPOS_DIR/$repo. The flat repos tree is a convention, not a law:
# in the Odoo devcontainer the one client repo is bind-mounted at
# /mnt/extra-addons, a name fixed by the addons path that can never equal the
# repo field, and no value of REPOS_DIR can bridge that. With repo_path set,
# neither repos-dir.sh nor the tree layout is consulted for this project at all.
# repo stays required and stays a bare folder name: it is the lookup key callers
# resolve by, independent of where the checkout happens to be mounted.
#
# release_assignee and release_reviewer are the GitHub logins an aggregation
# release PR is opened against for this project. They live here because they are
# a property of the project rather than of the run: asking for them mid-release
# is a question whose answer never changes, and the release route runs inside a
# subagent that cannot ask one. release_reviewer may be the literal `none`, which
# records a deliberate absence rather than an unanswered question.
#
# Exit codes: 0 ok | 2 user error | 3 project unmapped | 4 validation failure
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$SCRIPT_DIR/../../.." && pwd)}"
# The state dir has exactly one definition, in scripts/state-dir.sh - which is
# also what knows that a devcontainer supplies $ODOO_DEV_STATE_DIR from the
# personal-features bind mount (#884), so repo-map.json survives a rebuild.
# Restating the default here is how the two drift apart.
MAP="${REPO_MAP_FILE:-$("$PLUGIN_ROOT/scripts/state-dir.sh")/repo-map.json}"
# REPOS_DIR resolved lazily (only `add` with the repo-existence check needs it).

die() { echo "repo-map.sh: $*" >&2; exit 2; }

[ -f "$MAP" ] || die "map file not found: $MAP"

# node does all JSON work; bash never parses or emits JSON by hand.
run_node() { node --input-type=module -e "$1" -- "$MAP" "${@:2}"; }

VALIDATE_JS='
import { readFileSync } from "fs";
const file = process.argv[1];
let data;
try { data = JSON.parse(readFileSync(file, "utf8")); }
catch (e) { console.error("invalid JSON: " + e.message); process.exit(4); }
const KNOWN = ["repo", "repo_path", "default_branch", "odoo_version", "branch_flow", "flow_confirmed", "remote", "notes", "release_assignee", "release_reviewer"];
// The reserved first element of branch_flow, standing for the per-task branch.
// A colon cannot appear anywhere in a git ref name, so no branch can ever be
// spelled this way and the token needs no escaping rule to stay distinguishable.
const TASK_BRANCH = ":task";
// git check-ref-format, the part of it that applies to a single branch name. It
// holds every element other than the placeholder to being a branch somebody
// could actually create — which is what makes "placeholder or real branch?"
// answerable by looking at an element instead of at its position.
function refErr(b) {
  if (/[\x00-\x20~^:?*[\\\x7f]/.test(b)) return "contains a character git forbids in a branch name";
  if (b === "@" || b.includes("..") || b.includes("@{") || b.endsWith(".")) return "is not a legal git branch name";
  if (b.split("/").some((s) => !s || s.startsWith(".") || s.endsWith(".lock"))) return "is not a legal git branch name";
  return null;
}
const errs = [];
// Findings that do not invalidate the map but describe a claim nothing can
// honour. They ride on the success object rather than on the exit code.
const warns = [];
if (typeof data !== "object" || data === null || Array.isArray(data)) errs.push("root must be an object");
else {
  if (typeof data._doc !== "string") errs.push("_doc must be a string");
  if (typeof data.projects !== "object" || data.projects === null || Array.isArray(data.projects)) errs.push("projects must be an object");
  else for (const [name, entry] of Object.entries(data.projects)) {
    if (!name.trim()) errs.push("empty project name");
    if (typeof entry !== "object" || entry === null) { errs.push(`${name}: entry must be an object`); continue; }
    if (typeof entry.repo !== "string" || !entry.repo.trim()) errs.push(`${name}: repo is required`);
    else if (entry.repo.includes("/") || entry.repo.includes("..")) errs.push(`${name}: repo must be a bare folder name`);
    // repo_path overrides $REPOS_DIR/$repo, so it is only ever useful absolute:
    // a relative one would resolve against whatever directory the caller happens
    // to be standing in, which is exactly the guesswork this map exists to stop.
    if ("repo_path" in entry) {
      const p = entry.repo_path;
      if (typeof p !== "string" || !p.trim())
        errs.push(`${name}: repo_path must be a non-empty string (an absolute path to the checkout), or omit it`);
      else if (!p.startsWith("/"))
        errs.push(`${name}: repo_path must be an absolute path, got "${p}"`);
    }
    for (const k of ["default_branch", "odoo_version", "remote", "notes"])
      if (k in entry && typeof entry[k] !== "string") errs.push(`${name}: ${k} must be a string`);
    // Checked wherever branch_flow is or is not: a task PR is opened against
    // default_branch, so the one thing it can never be is the placeholder.
    if (entry.default_branch === TASK_BRANCH)
      errs.push(`${name}: default_branch cannot be "${TASK_BRANCH}" — that is the per-task placeholder, and a task PR needs a real branch to target`);
    // Recorded to be used unasked, so an empty one is worse than an absent one:
    // it reads as answered and names nobody. `none` is a real answer and passes.
    for (const k of ["release_assignee", "release_reviewer"])
      if (k in entry && (typeof entry[k] !== "string" || !entry[k].trim()))
        errs.push(`${name}: ${k} must be a non-empty string (a GitHub login, or "none" for release_reviewer)`);
    if ("flow_confirmed" in entry && typeof entry.flow_confirmed !== "boolean")
      errs.push(`${name}: flow_confirmed must be a boolean`);
    if ("branch_flow" in entry) {
      const flow = entry.branch_flow;
      if (!Array.isArray(flow)) errs.push(`${name}: branch_flow must be an array of branch names`);
      else {
        if (flow.length === 1) errs.push(`${name}: branch_flow needs at least a source and a target, or omit it`);
        for (const [i, b] of flow.entries()) {
          if (typeof b !== "string" || !b.trim()) { errs.push(`${name}: branch_flow entries must be non-empty strings`); continue; }
          // The placeholder is first or nowhere. Later elements are what work is
          // promoted INTO, and there is nothing to promote into a branch that is
          // cut fresh per task and exists on no remote.
          if (b === TASK_BRANCH) {
            if (i !== 0) errs.push(`${name}: "${TASK_BRANCH}" is the per-task placeholder and only ever the FIRST element of branch_flow, not element ${i}`);
            continue;
          }
          const bad = refErr(b);
          if (bad) errs.push(`${name}: branch_flow entry "${b}" ${bad} — every element but a leading "${TASK_BRANCH}" names a branch that has to exist on the remote`);
        }
        if (new Set(flow).size !== flow.length) errs.push(`${name}: branch_flow repeats a branch`);
        // default_branch is where task work is based, so it has to be ON the chain.
        if (flow.length && entry.default_branch && !flow.includes(entry.default_branch))
          errs.push(`${name}: default_branch "${entry.default_branch}" is not in branch_flow [${flow.join(", ")}]`);
        // The phantom this token exists to retire. A first element that is neither
        // the placeholder nor default_branch is claiming a shared branch that
        // nobody bases work on — historically the literal "dev", which resolved on
        // no remote. A warning, not an error: the map is still usable and every
        // other rule still holds, and the repair is one set-flow away.
        if (flow.length > 1 && flow[0] !== TASK_BRANCH && entry.default_branch && flow[0] !== entry.default_branch)
          warns.push(`${name}: branch_flow starts with "${flow[0]}", which is neither the "${TASK_BRANCH}" placeholder nor default_branch "${entry.default_branch}" — if it stands for the per-task branch rather than a branch on the remote, record it as the placeholder: repo-map.sh set-flow "${name}" "${[TASK_BRANCH, ...flow.slice(1)].join(",")}"`);
      }
    }
    if (entry.flow_confirmed === true && !(Array.isArray(entry.branch_flow) && entry.branch_flow.length))
      errs.push(`${name}: flow_confirmed is true but branch_flow is empty`);
    for (const k of Object.keys(entry))
      if (!KNOWN.includes(k)) errs.push(`${name}: unknown key ${k}`);
  }
}
if (errs.length) { console.error(errs.join("\n")); process.exit(4); }
// Printed only when asked, because every other command runs this as a precheck
// and an unasked-for line here would land in front of the JSON that command emits.
if (warns.length && process.env.REPO_MAP_WARN === "1") console.log(warns.join("\n"));
'

# Validation always runs against an explicit file, so a pending write can be
# checked before it is allowed to become the live map.
validate_file() { node --input-type=module -e "$VALIDATE_JS" -- "$1"; }

# Every mutation lands the same way: write tmp, validate tmp, .bak the live file,
# rename. A map that fails validation therefore never becomes the live map.
#
# Two rules hold at every one of the five call sites, and each is load-bearing:
#
#   tmp="$(mktemp "$MAP.tmp.XXXXXX")"
#   trap 'rm -f "$tmp"' EXIT
#
# mktemp rather than a fixed "$MAP.tmp", because a fixed path is shared: two
# invocations running at once write the same file and the second clobbers the
# first, so both exit 0 while one update is gone. Beside $MAP rather than in
# $TMPDIR, because the rename below is only atomic within one filesystem — and
# the mktemp'd file being mode 0600 is a deliberate tightening of a file that
# only its owner has any business reading.
#
# The trap is the only thing that removes it. `set -euo pipefail` aborts this
# function at the failing validate_file, before cp and mv, so a rejected write
# would otherwise leave its scratch file next to the map forever. After a
# successful mv there is nothing left at that path and the rm is a no-op.
commit_tmp() {
  local tmp="$1"
  local warns
  # Assigned on its own line so the exit status of a failed validation is the
  # status of this function: `local warns=$(...)` would swallow it and commit.
  warns="$(REPO_MAP_WARN=1 validate_file "$tmp")"
  # Warnings do not stop the write, but a chain whose first element resolves to
  # nothing should say so at the moment someone records it, not only on demand.
  [ -z "$warns" ] || echo "$warns" >&2
  cp "$MAP" "$MAP.bak"
  mv "$tmp" "$MAP"
}

# Both writers that accept --repo-path check it the same way, and check it here
# rather than at validation so the message names the flag the user typed instead
# of a key they never wrote. An explicit path answers the question outright: no
# tree to resolve, so repos-dir.sh is never called and its failure cannot block
# the write. An empty path is "not given" (or, for `set`, "unset it").
check_repo_path() { # check_repo_path <path> <repo_check 0|1>
  local path="$1" repo_check="$2"
  case "$path" in
    ""|/*) ;;
    *) die "--repo-path must be an absolute path, got: $path" ;;
  esac
  [ -n "$path" ] && [ "$repo_check" -eq 1 ] || return 0
  [ -d "$path" ] || die "repo folder not found: $path (use --no-repo-check only pre-rebuild)"
}

cmd="${1:-}"; shift || true
case "$cmd" in
  validate)
    # This is the one place warnings surface unprompted, because this is the one
    # command whose whole job is to have an opinion about the map. They ride on
    # the success object: the map is valid, and something in it still needs a
    # human. The bare object is kept byte-identical when there is nothing to say.
    warns="$(REPO_MAP_WARN=1 validate_file "$MAP")"
    if [ -n "$warns" ]; then
      node -e 'process.stdout.write("{\"ok\": true, \"warnings\": " + JSON.stringify(process.argv[1].split("\n")) + "}\n")' "$warns"
    else
      echo '{"ok": true}'
    fi
    ;;

  get)
    [ $# -eq 1 ] || die "usage: repo-map.sh get \"<project name>\""
    validate_file "$MAP"
    run_node '
      import { readFileSync } from "fs";
      const data = JSON.parse(readFileSync(process.argv[1], "utf8"));
      const entry = data.projects[process.argv[2]];
      if (!entry) { console.error("unmapped project: " + process.argv[2]); process.exit(3); }
      console.log(JSON.stringify({ project: process.argv[2], ...entry }));
    ' "$1"
    ;;

  list)
    validate_file "$MAP"
    run_node '
      import { readFileSync } from "fs";
      const data = JSON.parse(readFileSync(process.argv[1], "utf8"));
      console.log(JSON.stringify(data.projects, null, 2));
    '
    ;;

  add)
    [ $# -ge 2 ] || die "usage: repo-map.sh add \"<project name>\" <repo> [options]"
    project="$1"; repo="$2"; shift 2
    branch=""; version=""; notes=""; flow=""; remote=""; confirmed=false; repo_check=1
    rel_assignee=""; rel_reviewer=""; repo_path=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --default-branch)   branch="${2:?}"; shift 2 ;;
        --odoo-version)     version="${2:?}"; shift 2 ;;
        --branch-flow)      flow="${2:?}"; shift 2 ;;
        --remote)           remote="${2:?}"; shift 2 ;;
        --notes)            notes="${2:?}"; shift 2 ;;
        --release-assignee) rel_assignee="${2:?}"; shift 2 ;;
        --release-reviewer) rel_reviewer="${2:?}"; shift 2 ;;
        --repo-path)        repo_path="${2:?}"; shift 2 ;;
        --flow-confirmed)   confirmed=true; shift ;;
        --no-repo-check)    repo_check=0; shift ;;
        *) die "unknown option: $1" ;;
      esac
    done
    check_repo_path "$repo_path" "$repo_check"
    # Only `add` falls back to the flat tree, because only `add` knows the entry
    # has no repo_path at all; `set` may be leaving an existing one in place.
    if [ "$repo_check" -eq 1 ] && [ -z "$repo_path" ]; then
      REPOS_DIR="${REPOS_DIR:-$("$SCRIPT_DIR/repos-dir.sh" --raw)}"
      [ -d "$REPOS_DIR/$repo" ] || die "repo folder not found: $REPOS_DIR/$repo (pass --repo-path /abs/path when the checkout is not under the repos tree, or --no-repo-check pre-rebuild)"
    fi
    validate_file "$MAP"
    tmp="$(mktemp "$MAP.tmp.XXXXXX")"
    trap 'rm -f "$tmp"' EXIT
    run_node '
      import { readFileSync, writeFileSync } from "fs";
      const [file, project, repo, repoPath, branch, version, flow, remote, notes, confirmed, relAssignee, relReviewer, tmp] = process.argv.slice(1);
      const data = JSON.parse(readFileSync(file, "utf8"));
      if (data.projects[project]) { console.error("duplicate project: " + project); process.exit(2); }
      const entry = { repo };
      if (repoPath) entry.repo_path = repoPath;
      if (branch) entry.default_branch = branch;
      if (version) entry.odoo_version = version;
      if (flow) entry.branch_flow = flow.split(",").map((b) => b.trim()).filter(Boolean);
      if (confirmed === "true") entry.flow_confirmed = true;
      if (remote) entry.remote = remote;
      if (notes) entry.notes = notes;
      if (relAssignee) entry.release_assignee = relAssignee;
      if (relReviewer) entry.release_reviewer = relReviewer;
      data.projects[project] = entry;
      writeFileSync(tmp, JSON.stringify(data, null, 2) + "\n");
    ' "$project" "$repo" "$repo_path" "$branch" "$version" "$flow" "$remote" "$notes" "$confirmed" "$rel_assignee" "$rel_reviewer" "$tmp"
    commit_tmp "$tmp"
    echo "{\"ok\": true, \"added\": $(node -e 'console.log(JSON.stringify(process.argv[1]))' "$project")}"
    ;;

  set)
    # Merges into the existing entry. `add` only creates, so before this the only
    # way to change a field was remove + full re-add: lossy for every field left
    # off, and non-atomic because the remove committed first.
    [ $# -ge 1 ] || die "usage: repo-map.sh set \"<project name>\" [--default-branch B] [--odoo-version V] [--remote owner/repo] [--notes \"...\"] [--repo-path /abs/path] [--no-repo-check]"
    project="$1"; shift
    # Alternating key/value pairs, applied in order by node — so a repeated flag
    # simply lets the last one win, and an unnamed field is never written at all.
    updates=(); repo_path=""; repo_check=1
    while [ $# -gt 0 ]; do
      case "$1" in
        --default-branch|--odoo-version|--remote|--notes|--repo-path)
          [ $# -ge 2 ] || die "$1 needs a value (pass \"\" to unset the field)"
          key="${1#--}"; key="${key//-/_}"
          if [ "$key" = repo_path ]; then repo_path="$2"; fi
          updates+=("$key" "$2"); shift 2 ;;
        --branch-flow)
          die "branch_flow is set by: repo-map.sh set-flow \"$project\" \":task,UAT,main\" [--flow-confirmed]" ;;
        --release-assignee|--release-reviewer)
          die "release owners are set by: repo-map.sh set-release-owners \"$project\" <assignee> <reviewer|none>" ;;
        --no-repo-check) repo_check=0; shift ;;
        *) die "unknown option: $1" ;;
      esac
    done
    [ ${#updates[@]} -gt 0 ] || die "set needs at least one field: --default-branch, --odoo-version, --remote, --notes or --repo-path"
    check_repo_path "$repo_path" "$repo_check"
    validate_file "$MAP"
    tmp="$(mktemp "$MAP.tmp.XXXXXX")"
    trap 'rm -f "$tmp"' EXIT
    run_node '
      import { readFileSync, writeFileSync } from "fs";
      const [file, project, tmp, ...updates] = process.argv.slice(1);
      const data = JSON.parse(readFileSync(file, "utf8"));
      const entry = data.projects[project];
      if (!entry) { console.error("unmapped project: " + project); process.exit(3); }
      for (let i = 0; i < updates.length; i += 2) {
        const [key, value] = [updates[i], updates[i + 1]];
        // "" is the only way a merge can say "remove this key". An empty string
        // left in place would validate but read as an answer nobody gave.
        if (value === "") delete entry[key]; else entry[key] = value;
      }
      writeFileSync(tmp, JSON.stringify(data, null, 2) + "\n");
    ' "$project" "$tmp" "${updates[@]}"
    commit_tmp "$tmp"
    bash "$SCRIPT_DIR/repo-map.sh" get "$project"
    ;;

  set-flow)
    [ $# -ge 2 ] || die "usage: repo-map.sh set-flow \"<project name>\" \":task,UAT,main\" [--flow-confirmed]"
    project="$1"; flow="$2"; shift 2
    confirmed=false
    while [ $# -gt 0 ]; do
      case "$1" in
        --flow-confirmed) confirmed=true; shift ;;
        *) die "unknown option: $1" ;;
      esac
    done
    validate_file "$MAP"
    tmp="$(mktemp "$MAP.tmp.XXXXXX")"
    trap 'rm -f "$tmp"' EXIT
    run_node '
      import { readFileSync, writeFileSync } from "fs";
      const [file, project, flow, confirmed, tmp] = process.argv.slice(1);
      const data = JSON.parse(readFileSync(file, "utf8"));
      const entry = data.projects[project];
      if (!entry) { console.error("unmapped project: " + project); process.exit(3); }
      entry.branch_flow = flow.split(",").map((b) => b.trim()).filter(Boolean);
      if (confirmed === "true") entry.flow_confirmed = true; else delete entry.flow_confirmed;
      writeFileSync(tmp, JSON.stringify(data, null, 2) + "\n");
    ' "$project" "$flow" "$confirmed" "$tmp"
    commit_tmp "$tmp"
    bash "$SCRIPT_DIR/repo-map.sh" get "$project"
    ;;

  set-release-owners)
    # `add` only creates, so without this the two release fields could be recorded
    # on a new project and never on an existing one — which is every project.
    [ $# -eq 3 ] || die "usage: repo-map.sh set-release-owners \"<project name>\" <assignee> <reviewer|none>"
    project="$1"; rel_assignee="$2"; rel_reviewer="$3"
    validate_file "$MAP"
    tmp="$(mktemp "$MAP.tmp.XXXXXX")"
    trap 'rm -f "$tmp"' EXIT
    run_node '
      import { readFileSync, writeFileSync } from "fs";
      const [file, project, assignee, reviewer, tmp] = process.argv.slice(1);
      const data = JSON.parse(readFileSync(file, "utf8"));
      const entry = data.projects[project];
      if (!entry) { console.error("unmapped project: " + project); process.exit(3); }
      entry.release_assignee = assignee;
      entry.release_reviewer = reviewer;
      writeFileSync(tmp, JSON.stringify(data, null, 2) + "\n");
    ' "$project" "$rel_assignee" "$rel_reviewer" "$tmp"
    commit_tmp "$tmp"
    bash "$SCRIPT_DIR/repo-map.sh" get "$project"
    ;;

  remove)
    [ $# -eq 1 ] || die "usage: repo-map.sh remove \"<project name>\""
    validate_file "$MAP"
    tmp="$(mktemp "$MAP.tmp.XXXXXX")"
    trap 'rm -f "$tmp"' EXIT
    run_node '
      import { readFileSync, writeFileSync } from "fs";
      const [file, project, tmp] = process.argv.slice(1);
      const data = JSON.parse(readFileSync(file, "utf8"));
      if (!data.projects[project]) { console.error("unmapped project: " + project); process.exit(3); }
      delete data.projects[project];
      writeFileSync(tmp, JSON.stringify(data, null, 2) + "\n");
    ' "$1" "$tmp"
    commit_tmp "$tmp"
    echo "{\"ok\": true, \"removed\": $(node -e 'console.log(JSON.stringify(process.argv[1]))' "$1")}"
    ;;

  *)
    die "unknown command: '$cmd' (get|list|add|set|set-flow|set-release-owners|remove|validate)"
    ;;
esac
