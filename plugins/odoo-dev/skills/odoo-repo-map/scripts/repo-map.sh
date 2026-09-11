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
#   repo-map.sh set-flow "<project name>" "dev,UAT,main" [--flow-confirmed]
#   repo-map.sh set-release-owners "<project name>" <assignee> <reviewer|none>
#   repo-map.sh remove "<project name>"
#   repo-map.sh validate
#
# branch_flow is the ordered environment chain, first element the branch task
# work starts from and LAST element production. It exists because the free-text
# notes field could only ever be read by a human: "flow: dev -> UAT -> main" is
# obvious prose and unusable as data, so promotion decisions could not be
# computed. flow_confirmed records whether a human has actually vouched for the
# chain — a flow parsed out of old notes is a hypothesis, and a wrong last
# element points a release at production.
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
const KNOWN = ["repo", "default_branch", "odoo_version", "branch_flow", "flow_confirmed", "remote", "notes", "release_assignee", "release_reviewer"];
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
    rel_assignee=""; rel_reviewer=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --default-branch)   branch="${2:?}"; shift 2 ;;
        --odoo-version)     version="${2:?}"; shift 2 ;;
        --branch-flow)      flow="${2:?}"; shift 2 ;;
        --remote)           remote="${2:?}"; shift 2 ;;
        --notes)            notes="${2:?}"; shift 2 ;;
        --release-assignee) rel_assignee="${2:?}"; shift 2 ;;
        --release-reviewer) rel_reviewer="${2:?}"; shift 2 ;;
        --flow-confirmed)   confirmed=true; shift ;;
        --no-repo-check)    repo_check=0; shift ;;
        *) die "unknown option: $1" ;;
      esac
    done
    if [ "$repo_check" -eq 1 ]; then
      REPOS_DIR="${REPOS_DIR:-$("$SCRIPT_DIR/repos-dir.sh" --raw)}"
      [ -d "$REPOS_DIR/$repo" ] || die "repo folder not found: $REPOS_DIR/$repo (use --no-repo-check only pre-rebuild)"
    fi
    run_node "$VALIDATE_JS"
    tmp="$MAP.tmp"
    run_node '
      import { readFileSync, writeFileSync } from "fs";
      const [file, project, repo, branch, version, flow, remote, notes, confirmed, relAssignee, relReviewer, tmp] = process.argv.slice(1);
      const data = JSON.parse(readFileSync(file, "utf8"));
      if (data.projects[project]) { console.error("duplicate project: " + project); process.exit(2); }
      const entry = { repo };
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
    ' "$project" "$repo" "$branch" "$version" "$flow" "$remote" "$notes" "$confirmed" "$rel_assignee" "$rel_reviewer" "$tmp"
    commit_tmp "$tmp"
    echo "{\"ok\": true, \"added\": $(node -e 'console.log(JSON.stringify(process.argv[1]))' "$project")}"
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
    die "unknown command: '$cmd' (get|list|add|set-flow|set-release-owners|remove|validate)"
    ;;
esac
