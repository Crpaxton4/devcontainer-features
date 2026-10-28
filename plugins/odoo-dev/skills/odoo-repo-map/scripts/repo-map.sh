#!/usr/bin/env bash
# repo-map.sh — the sanctioned way to read or edit repo-map.json. Each verb is
# one `odoo-dev repo-map ...` call. The CLI validates, backs up and writes the
# file; this script only maps the arguments.
#
# The map lives in $ODOO_DEV_HOME (default ~/.config/odoo-dev). The first write
# creates it.
#
# Usage:
#   repo-map.sh get "<project name>" [--json]
#   repo-map.sh list [--json]
#   repo-map.sh add "<project name>" <repo> --odoo-version V [FIELDS] [--json]
#   repo-map.sh set "<project name>" [--repo R] [--odoo-version V] [FIELDS] [--json]
#   repo-map.sh set-flow "<project name>" "task,UAT,main" [--flow-confirmed]
#   repo-map.sh set-release-owners "<project name>" <assignee> <reviewer|none>
#   repo-map.sh remove "<project name>" [--json]
#   repo-map.sh validate [--json]
#
# FIELDS: [--aliases A,B] [--branch-flow task,UAT,main] [--upgrade-branch 19.0=BRANCH]...
#         [--release-assignee LOGIN] [--release-reviewer LOGIN|none]
#
# <repo> is a folder name under the repos dir, or an absolute path. `add` does
# not look for the folder. `set` keeps every field it is not given; the CLI
# `set` replaces a whole entry, so this script reads the entry first.
# `--upgrade-branch 19.0=` removes that series. `--flow-confirmed` is accepted
# and ignored: a confirmed flow is no longer recorded.
#
# Exit codes: 0 ok | 2 usage | 3 project unmapped | 4 validation failure
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$SCRIPT_DIR/../../.." && pwd)}"
JSON_GET="$PLUGIN_ROOT/scripts/json-get.sh"

die() { echo "repo-map.sh: $*" >&2; exit 2; }

[ $# -ge 1 ] || die "usage: repo-map.sh get|list|add|set|set-flow|set-release-owners|remove|validate ..."
verb="$1"; shift

# Parse the field options shared by add and set into the variables below.
repo=""; version=""; aliases=""; flow=""; assignee=""; reviewer=""
have_repo=0; have_version=0; have_aliases=0; have_flow=0; have_assignee=0; have_reviewer=0
upgrades=(); json=()
parse_fields() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --repo) repo="${2:?}"; have_repo=1; shift 2 ;;
      --odoo-version) version="${2:?}"; have_version=1; shift 2 ;;
      --aliases) aliases="${2?}"; have_aliases=1; shift 2 ;;
      --branch-flow) flow="${2?}"; have_flow=1; shift 2 ;;
      --upgrade-branch) upgrades+=("${2:?}"); shift 2 ;;
      --release-assignee) assignee="${2?}"; have_assignee=1; shift 2 ;;
      --release-reviewer) reviewer="${2?}"; have_reviewer=1; shift 2 ;;
      --flow-confirmed) echo "repo-map.sh: flow confirmation is no longer recorded; ignoring --flow-confirmed" >&2; shift ;;
      --json) json=(--json); shift ;;
      *) die "unknown option: $1" ;;
    esac
  done
}

# Write one entry: the stored fields, overridden by the ones given.
# write_entry <name> [<current entry json>]
write_entry() {
  local name="$1" cur="${2:-}" pair series
  local -A up=()
  if [ -n "$cur" ]; then
    [ "$have_repo" = 1 ] || repo="$("$JSON_GET" d.repo <<<"$cur")"
    [ "$have_version" = 1 ] || version="$("$JSON_GET" d.odoo_version <<<"$cur")"
    [ "$have_aliases" = 1 ] || aliases="$("$JSON_GET" 'd.aliases.join(",")' <<<"$cur")"
    [ "$have_flow" = 1 ] || flow="$("$JSON_GET" 'd.branch_flow.join(",")' <<<"$cur")"
    [ "$have_assignee" = 1 ] || assignee="$("$JSON_GET" d.release_assignee <<<"$cur")"
    [ "$have_reviewer" = 1 ] || reviewer="$("$JSON_GET" d.release_reviewer <<<"$cur")"
    while IFS= read -r pair; do
      [ -n "$pair" ] && up["${pair%%=*}"]="${pair#*=}"
    done < <("$JSON_GET" -l 'Object.entries(d.upgrade_branch).map(([k, v]) => k + "=" + v)' <<<"$cur")
  fi
  for pair in "${upgrades[@]+"${upgrades[@]}"}"; do
    case "$pair" in *=*) ;; *) die "--upgrade-branch needs SERIES=BRANCH, got: $pair" ;; esac
    series="${pair%%=*}"
    if [ -z "${pair#*=}" ]; then unset "up[$series]"; else up["$series"]="${pair#*=}"; fi
  done
  [ -n "$repo" ] || die "repo is required"
  [ -n "$version" ] || die "--odoo-version is required"
  local args=(set "$name" --repo "$repo" --odoo-version "$version" --aliases "$aliases"
    --branch-flow "$flow" --release-assignee "$assignee" --release-reviewer "$reviewer")
  for series in "${!up[@]}"; do args+=(--upgrade-branch "$series=${up[$series]}"); done
  odoo-dev repo-map "${args[@]}" "${json[@]+"${json[@]}"}"
}

# The stored entry for <name>; exit 3 when there is none.
current() { odoo-dev repo-map get "$1" --json; }

case "$verb" in
  get | remove)
    [ $# -ge 1 ] || die "usage: repo-map.sh $verb \"<project name>\" [--json]"
    exec odoo-dev repo-map "$verb" "$@"
    ;;
  list | validate)
    exec odoo-dev repo-map "$verb" "$@"
    ;;
  add)
    [ $# -ge 2 ] || die "usage: repo-map.sh add \"<project name>\" <repo> --odoo-version V [FIELDS]"
    name="$1"; repo="$2"; have_repo=1; shift 2
    parse_fields "$@"
    if odoo-dev repo-map get "$name" --json >/dev/null 2>&1; then
      die "project already mapped: $name (use set to change it)"
    fi
    write_entry "$name"
    ;;
  set)
    [ $# -ge 1 ] || die "usage: repo-map.sh set \"<project name>\" [FIELDS]"
    name="$1"; shift
    parse_fields "$@"
    cur="$(current "$name")" || exit $?
    write_entry "$("$JSON_GET" d.name <<<"$cur")" "$cur"
    ;;
  set-flow)
    [ $# -ge 2 ] || die "usage: repo-map.sh set-flow \"<project name>\" \"task,UAT,main\""
    name="$1"; flow="$2"; have_flow=1; shift 2
    parse_fields "$@"
    cur="$(current "$name")" || exit $?
    write_entry "$("$JSON_GET" d.name <<<"$cur")" "$cur"
    ;;
  set-release-owners)
    [ $# -eq 3 ] || die "usage: repo-map.sh set-release-owners \"<project name>\" <assignee> <reviewer|none>"
    name="$1"; assignee="$2"; reviewer="$3"; have_assignee=1; have_reviewer=1
    cur="$(current "$name")" || exit $?
    write_entry "$("$JSON_GET" d.name <<<"$cur")" "$cur"
    ;;
  *) die "unknown verb: $verb" ;;
esac
