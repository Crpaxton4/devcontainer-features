#!/usr/bin/env bash
# project-resolve.sh — one Odoo project (or repo folder) -> everything the rest
# of the delivery suite needs to act on it. A thin wrapper over
# `odoo-dev repo-map resolve`.
#
# Usage: project-resolve.sh "<project name|alias|repo folder>" [--next-after <branch>] [--series NN.0]
#
# The query is tried as a project name, an alias, then a repo folder name. A
# folder that maps to SEVERAL projects is ambiguous (exit 6): name the project.
#
# --next-after <branch> answers "where does work on <branch> get promoted to",
# from branch_flow. <branch> is an element of the flow. For the first hop out of
# a per-task branch pass `task`, the first element of a flow that starts from a
# per-task branch. next_env is null at the production branch (the last element).
#
# --series NN.0 fills upgrade_branch: the entry's upgrade_branch[NN.0] when set,
# else the last element of the flow. Without --series the key is null.
#
# Last stdout line: {"project","repo","repo_root","origin","odoo_version",
#   "branch_flow":[],"base_branch","upgrade_branch","next_env","at_production",
#   "release_assignee","release_reviewer"}
#
# branch_flow is the effective flow: the entry's own, else the map defaults.
# base_branch is what a task branch is cut from and its PR targets: the first
# element after `task` that exists on origin (a missing branch is skipped).
# It is null when there is no checkout or no such branch.
# repo_root is the checkout on this machine, or null. release_assignee and
# release_reviewer fall back to the map defaults and are null when neither is
# set. Null means unanswered; the caller has to ask.
#
# Exit codes: 0 ok | 2 usage | 3 unmapped | 4 --next-after cannot answer | 6 ambiguous
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$SCRIPT_DIR/../../.." && pwd)}"
JSON_GET="$PLUGIN_ROOT/scripts/json-get.sh"

usage() { echo "usage: project-resolve.sh \"<project|repo>\" [--next-after <branch>] [--series NN.0]" >&2; exit 2; }

[ $# -ge 1 ] || usage
key="$1"; shift
next_after=""; series=""
while [ $# -gt 0 ]; do
  case "$1" in
    --next-after) next_after="${2:?}"; shift 2 ;;
    --series)
      series="${2:?}"; shift 2
      case "$series" in
        [0-9]*.0) ;;
        *) echo "--series must be an Odoo series like 19.0, got: $series" >&2; exit 2 ;;
      esac
      case "${series%.0}" in *[!0-9]*) echo "--series must be an Odoo series like 19.0, got: $series" >&2; exit 2 ;; esac ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

# A missing repos tree is not an error: the metadata is still worth returning.
repos_dir="$(REPOS_DIR="${REPOS_DIR:-}" "$SCRIPT_DIR/repos-dir.sh" --raw 2>/dev/null || true)"
args=(repo-map resolve "$key" --json)
[ -z "$repos_dir" ] || args+=(--repos-dir "$repos_dir")

rc=0
resolved="$(odoo-dev "${args[@]}")" || rc=$?
[ "$rc" -eq 0 ] || [ "$rc" -eq 3 ] || exit "$rc"

while IFS= read -r warning; do
  case "$warning" in
    ambiguous:*)
      echo "$warning" >&2
      exit 6 ;;
    "no repo-map entry"*) ;;
    *) echo "project-resolve.sh: $warning" >&2 ;;
  esac
done < <("$JSON_GET" -l d.warnings <<<"$resolved")

if [ "$rc" -eq 3 ]; then
  echo "unmapped project or repo: $key" >&2
  echo "known projects:" >&2
  odoo-dev repo-map list --json | "$JSON_GET" -l 'd.map((p) => "  " + p.name)' >&2
  echo "Stop and ask the user; never guess a repo for an unknown project." >&2
  echo "To record what is mechanically derivable about a checkout first: project-bootstrap.sh \"<checkout path>\"" >&2
  exit 3
fi

defaults="$(odoo-dev repo-map defaults --json)"
mapfile -t flow < <("$JSON_GET" -l 'd.project.branch_flow.length ? d.project.branch_flow : JSON.parse(a.defaults).branch_flow' defaults="$defaults" <<<"$resolved")

# base_branch: the first element after `task` that exists on origin. A missing
# branch is skipped. Null without a checkout, or when no element exists there.
base_branch=""
repo_root="$("$JSON_GET" d.repo_root <<<"$resolved")"
if [ -n "$repo_root" ]; then
  for branch in "${flow[@]}"; do
    [ "$branch" != "task" ] || continue
    if git -C "$repo_root" ls-remote --exit-code --heads origin "$branch" >/dev/null 2>&1; then
      base_branch="$branch"
      break
    fi
  done
fi

next_env=""; at_production=""
if [ -n "$next_after" ]; then
  index=-1
  for i in "${!flow[@]}"; do
    [ "${flow[$i]}" = "$next_after" ] && { index="$i"; break; }
  done
  if [ "$index" -eq -1 ]; then
    hint=""
    [ "${flow[0]}" != "task" ] || hint=" — for the first hop out of a per-task branch pass: --next-after task"
    echo "branch \"$next_after\" is not in branch_flow [${flow[*]}] for \"$key\"$hint" >&2
    exit 4
  fi
  if [ "$index" -eq $((${#flow[@]} - 1)) ]; then
    at_production=true
  else
    at_production=false
    next_env="${flow[$((index + 1))]}"
  fi
fi

"$JSON_GET" '({
  project: d.project.name,
  repo: d.project.repo,
  repo_root: d.repo_root,
  origin: d.origin,
  odoo_version: d.project.odoo_version || null,
  branch_flow: a.flow.split(","),
  base_branch: a.base_branch || null,
  upgrade_branch: a.series ? d.project.upgrade_branch[a.series] || a.flow.split(",").at(-1) : null,
  next_env: a.next_env || null,
  at_production: a.at_production ? a.at_production === "true" : null,
  release_assignee: d.project.release_assignee || JSON.parse(a.defaults).release_assignee || null,
  release_reviewer: d.project.release_reviewer || JSON.parse(a.defaults).release_reviewer || null,
})' defaults="$defaults" series="$series" next_env="$next_env" at_production="$at_production" \
  base_branch="$base_branch" flow="$(IFS=,; echo "${flow[*]}")" <<<"$resolved"
