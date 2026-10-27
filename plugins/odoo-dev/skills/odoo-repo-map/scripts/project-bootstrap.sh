#!/usr/bin/env bash
# project-bootstrap.sh — record the mechanically derivable half of a repo-map
# entry for a checkout nobody has mapped yet, so an unmapped project is a PHASE
# of a run rather than the end of it.
#
# Usage: project-bootstrap.sh "<checkout path>" [--project "<project name>"]
#
# Two fields are readable off the checkout:
#
#   repo           the checkout folder name when the repos dir holds it under
#                  that name, else the absolute path (a bind mount such as
#                  /mnt/extra-addons has a name that cannot match)
#   odoo_version   the majority NN.0 series across the checkout's
#                  __manifest__.py files; failing that, the checked-out branch
#                  name when it is itself spelled NN.0
#
# The project name is --project, defaulting to the folder name. The branch flow
# is not derived: the entry inherits the map defaults until a human sets one
# with `repo-map.sh set-flow`. The write is `repo-map.sh add`.
#
# A checkout that already has an entry is not added again. Its entry is printed
# and the exit code is 3.
#
# Last stdout: the resulting entry, `repo-map.sh get --json`.
#
# Exit codes: 0 entry written | 2 usage, not a git checkout, or no odoo_version
#           | 3 this checkout is already mapped (its entry is printed)
#           | anything else is repo-map.sh's own code (4 validation)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() { echo "project-bootstrap.sh: $*" >&2; exit 2; }

[ $# -ge 1 ] || die "usage: project-bootstrap.sh \"<checkout path>\" [--project \"<project name>\"]"
checkout="$1"; shift
project=""
while [ $# -gt 0 ]; do
  case "$1" in
    --project) project="${2:?}"; shift 2 ;;
    *) die "unknown option: $1" ;;
  esac
done

[ -d "$checkout" ] || die "checkout not found: $checkout"
checkout_dir="$(cd "$checkout" && pwd)"
folder="$(basename "$checkout_dir")"
[ "$folder" != "/" ] || die "cannot derive a repo folder name from: $checkout"
git -C "$checkout_dir" rev-parse --git-dir >/dev/null 2>&1 \
  || die "not a git checkout: $checkout_dir"

# Already mapped? Resolve from inside the checkout: the CLI matches its origin.
rc=0
resolved="$(cd "$checkout_dir" && odoo-dev repo-map resolve --json)" || rc=$?
if [ "$rc" -eq 0 ]; then
  owner="$("$SCRIPT_DIR/../../../scripts/json-get.sh" d.project.name <<<"$resolved")"
  echo "project-bootstrap.sh: $checkout_dir is already mapped as \"$owner\" — resolve by that name: project-resolve.sh \"$owner\"" >&2
  bash "$SCRIPT_DIR/repo-map.sh" get "$owner" --json
  exit 3
fi
[ "$rc" -eq 3 ] || exit "$rc"
warning="$("$SCRIPT_DIR/../../../scripts/json-get.sh" 'd.warnings[0]' <<<"$resolved")"
case "$warning" in
  ambiguous:*) echo "project-bootstrap.sh: $checkout_dir is already mapped: $warning" >&2; exit 3 ;;
esac

[ -n "$project" ] || project="$folder"

# repo: the bare folder name only when the repos dir would find it again.
repo="$checkout_dir"
repos_dir="$(REPOS_DIR="${REPOS_DIR:-}" "$SCRIPT_DIR/repos-dir.sh" --raw 2>/dev/null || true)"
if [ -n "$repos_dir" ] && [ -d "$repos_dir/$folder" ] \
  && [ "$(cd "$repos_dir/$folder" && pwd -P)" = "$(cd "$checkout_dir" && pwd -P)" ]; then
  repo="$folder"
fi

# odoo_version: the series is a property of the code, so count the manifests
# rather than trust one. A tie is no answer. The bounded depth reaches a flat
# addons repo without descending into a vendored Odoo source tree.
odoo_version="$(find "$checkout_dir" -maxdepth 4 \( -name .git -o -name node_modules \) -prune -o -name __manifest__.py -print0 \
  | xargs -0 -r grep -hoE "['\"]version['\"][[:space:]]*:[[:space:]]*['\"][0-9]{1,2}\.0" \
  | grep -oE '[0-9]{1,2}\.0$' | sort | uniq -c | sort -rn | head -2 || true)"
top="$(printf '%s\n' "$odoo_version" | sed -n 1p | awk '{print $1}')"
second="$(printf '%s\n' "$odoo_version" | sed -n 2p | awk '{print $1}')"
if [ -n "$top" ] && [ "$top" -gt "${second:-0}" ]; then
  odoo_version="$(printf '%s\n' "$odoo_version" | sed -n 1p | awk '{print $2}')"
else
  branch="$(git -C "$checkout_dir" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
  case "$branch" in
    [0-9].0 | [0-9][0-9].0) odoo_version="$branch" ;;
    *) die "cannot derive odoo_version from $checkout_dir; add it by hand: repo-map.sh add \"$project\" \"$repo\" --odoo-version NN.0" ;;
  esac
fi

bash "$SCRIPT_DIR/repo-map.sh" add "$project" "$repo" --odoo-version "$odoo_version" >&2
bash "$SCRIPT_DIR/repo-map.sh" get "$project" --json
