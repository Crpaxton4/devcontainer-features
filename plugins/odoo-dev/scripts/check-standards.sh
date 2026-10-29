#!/usr/bin/env bash
# check-standards.sh — the code-standards gate of the implement run (#1302).
#
# Usage: check-standards.sh <worktree> [<base>]
#
# Checks only the Python files that the worktree HEAD added, copied, modified or
# renamed against <base> (three dots: from the merge base). Legacy code that the
# task did not touch never blocks a task. An empty or missing <base> falls back to
# origin/HEAD, with a WARN line on stderr.
#
# The rules live in check_standards.py (standard library only, one python3 call):
#   one-model-per-file  more than one Odoo model class in a file of models/
#   docstring           a function or method without a docstring
#   nesting             if in if, or any nesting except for-in-for and if-in-for
#   max-loc             more than 25 logical lines in a function
#   complexity          cyclomatic complexity above 5 in a function
#
# Stdout: one finding per line, `file:line rule message`, paths relative to <worktree>.
# Exit: 0 clean | 1 findings | 2 usage, not a git work tree, unresolvable base, or no python3
set -uo pipefail

[ $# -ge 1 ] && [ $# -le 2 ] || { echo "usage: check-standards.sh <worktree> [<base>]" >&2; exit 2; }
wt="$1"; base="${2:-}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

git -C "$wt" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || { echo "check-standards.sh: not a git work tree: $wt" >&2; exit 2; }
command -v python3 >/dev/null 2>&1 \
  || { echo "check-standards.sh: python3 is missing" >&2; exit 2; }

base_ref=""
if [ -z "$base" ]; then
  echo "WARN base_branch is empty: using origin/HEAD" >&2
  git -C "$wt" show-ref --verify --quiet refs/remotes/origin/HEAD && base_ref="refs/remotes/origin/HEAD"
else
  for ref in "refs/remotes/origin/$base" "refs/heads/$base"; do
    if git -C "$wt" show-ref --verify --quiet "$ref"; then base_ref="$ref"; break; fi
  done
  [ -n "$base_ref" ] || ! git -C "$wt" rev-parse --verify --quiet "$base^{commit}" >/dev/null 2>&1 || base_ref="$base"
fi
[ -n "$base_ref" ] || { echo "check-standards.sh: base '${base:-origin/HEAD}' does not resolve in $wt" >&2; exit 2; }

files=()
while IFS= read -r f; do
  case "$f" in *.py) [ -f "$wt/$f" ] && files+=("$f") ;; esac
done < <(git -C "$wt" diff --name-only --diff-filter=ACMR "$base_ref...HEAD")

[ "${#files[@]}" -gt 0 ] || exit 0
cd "$wt" || exit 2
exec python3 -I "$here/check_standards.py" "${files[@]}"
