#!/usr/bin/env bash
# repo-map.sh — the ONLY sanctioned way to read or edit repo-map.json.
# Guarantees: schema-validated content, atomic writes (tmp + mv), one .bak of
# the prior version, distinct exit codes.
#
# Usage:
#   repo-map.sh get "<project name>"
#   repo-map.sh list
#   repo-map.sh add "<project name>" <repo> [--default-branch B] [--odoo-version V]
#                                          [--branch-flow "dev,UAT,main"] [--flow-confirmed]
#                                          [--remote owner/repo] [--notes "..."] [--no-repo-check]
#                                          [--release-assignee LOGIN] [--release-reviewer LOGIN|none]
#                                          [--repo-path /abs/path/to/checkout]
#   repo-map.sh set "<project name>" [--default-branch B] [--odoo-version V]
#                                    [--remote owner/repo] [--notes "..."]
#                                    [--repo-path /abs/path] [--no-repo-check]
#   repo-map.sh set-flow "<project name>" "dev,UAT,main" [--flow-confirmed]
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
# branch_flow is the ordered environment chain, first element the branch task
# work starts from and LAST element production. It exists because the free-text
# notes field could only ever be read by a human: "flow: dev -> UAT -> main" is
# obvious prose and unusable as data, so promotion decisions could not be
# computed. flow_confirmed records whether a human has actually vouched for the
# chain — a flow parsed out of old notes is a hypothesis, and a wrong last
# element points a release at production.
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
MAP="${REPO_MAP_FILE:-${ODOO_DEV_STATE_DIR:-$HOME/.local/share/odoo-dev}/repo-map.json}"
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
const errs = [];
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
        for (const b of flow)
          if (typeof b !== "string" || !b.trim()) errs.push(`${name}: branch_flow entries must be non-empty strings`);
        if (new Set(flow).size !== flow.length) errs.push(`${name}: branch_flow repeats a branch`);
        // default_branch is where task work is based, so it has to be ON the chain.
        if (flow.length && entry.default_branch && !flow.includes(entry.default_branch))
          errs.push(`${name}: default_branch "${entry.default_branch}" is not in branch_flow [${flow.join(", ")}]`);
      }
    }
    if (entry.flow_confirmed === true && !(Array.isArray(entry.branch_flow) && entry.branch_flow.length))
      errs.push(`${name}: flow_confirmed is true but branch_flow is empty`);
    for (const k of Object.keys(entry))
      if (!KNOWN.includes(k)) errs.push(`${name}: unknown key ${k}`);
  }
}
if (errs.length) { console.error(errs.join("\n")); process.exit(4); }
'

# Every mutation lands the same way: write tmp, validate tmp, .bak the live file,
# rename. A map that fails validation therefore never becomes the live map.
commit_tmp() {
  local tmp="$1"
  REPO_MAP_FILE="$tmp" bash "$SCRIPT_DIR/repo-map.sh" validate >/dev/null
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
    run_node "$VALIDATE_JS"
    echo '{"ok": true}'
    ;;

  get)
    [ $# -eq 1 ] || die "usage: repo-map.sh get \"<project name>\""
    run_node "$VALIDATE_JS"
    run_node '
      import { readFileSync } from "fs";
      const data = JSON.parse(readFileSync(process.argv[1], "utf8"));
      const entry = data.projects[process.argv[2]];
      if (!entry) { console.error("unmapped project: " + process.argv[2]); process.exit(3); }
      console.log(JSON.stringify({ project: process.argv[2], ...entry }));
    ' "$1"
    ;;

  list)
    run_node "$VALIDATE_JS"
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
    run_node "$VALIDATE_JS"
    tmp="$MAP.tmp"
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
          die "branch_flow is set by: repo-map.sh set-flow \"$project\" \"dev,UAT,main\" [--flow-confirmed]" ;;
        --release-assignee|--release-reviewer)
          die "release owners are set by: repo-map.sh set-release-owners \"$project\" <assignee> <reviewer|none>" ;;
        --no-repo-check) repo_check=0; shift ;;
        *) die "unknown option: $1" ;;
      esac
    done
    [ ${#updates[@]} -gt 0 ] || die "set needs at least one field: --default-branch, --odoo-version, --remote, --notes or --repo-path"
    check_repo_path "$repo_path" "$repo_check"
    run_node "$VALIDATE_JS"
    tmp="$MAP.tmp"
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
    [ $# -ge 2 ] || die "usage: repo-map.sh set-flow \"<project name>\" \"dev,UAT,main\" [--flow-confirmed]"
    project="$1"; flow="$2"; shift 2
    confirmed=false
    while [ $# -gt 0 ]; do
      case "$1" in
        --flow-confirmed) confirmed=true; shift ;;
        *) die "unknown option: $1" ;;
      esac
    done
    run_node "$VALIDATE_JS"
    tmp="$MAP.tmp"
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
    run_node "$VALIDATE_JS"
    tmp="$MAP.tmp"
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
    run_node "$VALIDATE_JS"
    tmp="$MAP.tmp"
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
