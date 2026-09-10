#!/usr/bin/env bash
# repos-dir.sh — resolve the repos tree deterministically. Single source of
# truth for every skill and script in the delivery suite.
#
# Resolution order:
#   1. $REPOS_DIR (must exist if set)
#   2. $PWD, if it looks like the repos tree (>= 2 immediate git-repo subdirs)
#   3. $REPOS_DIR_CANDIDATES (colon-separated) or the built-in candidate list
#
# The candidate list is a convenience for interactive use, never a contract:
# callers that care set REPOS_DIR. Keeping it overridable means a machine with a
# differently-shaped home does not need this file edited.
#
# Last stdout line: {"repos_dir": "<abs path>"}; exit 1 when unresolvable.
# --raw prints the bare path instead, for the sibling scripts that assign it to a
# shell variable — a JSON parse per script invocation buys nothing there.
set -euo pipefail

raw=false
[ "${1:-}" = "--raw" ] && { raw=true; shift; }
[ $# -eq 0 ] || { echo "usage: repos-dir.sh [--raw]" >&2; exit 2; }

looks_like_repos_tree() {
  local d="$1" n=0 g
  [ -d "$d" ] || return 1
  for g in "$d"/*/.git; do
    [ -e "$g" ] && n=$((n + 1))
    [ "$n" -ge 2 ] && return 0
  done
  return 1
}

emit() {
  if [ "$raw" = true ]; then printf '%s\n' "$1"; else printf '{"repos_dir": "%s"}\n' "$1"; fi
}

if [ -n "${REPOS_DIR:-}" ]; then
  [ -d "$REPOS_DIR" ] || { echo "REPOS_DIR is set but not a directory: $REPOS_DIR" >&2; exit 1; }
  emit "$REPOS_DIR"
  exit 0
fi

IFS=':' read -r -a candidates <<< "${REPOS_DIR_CANDIDATES:-/home/cpaxton/repos:/workspaces/repos:$HOME/repos}"
for cand in "$PWD" "${candidates[@]}"; do
  [ -n "$cand" ] || continue
  if looks_like_repos_tree "$cand"; then
    emit "$cand"
    exit 0
  fi
done

echo "could not resolve the repos tree (no candidate has >= 2 git repos); set REPOS_DIR explicitly" >&2
exit 1
