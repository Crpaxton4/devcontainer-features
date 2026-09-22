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
# A flat tree is an assumption this resolver cannot always satisfy: it needs a
# directory whose immediate subdirectory names equal the `repo` field, and a
# bind mount (the Odoo devcontainer's /mnt/extra-addons) has a name fixed by the
# addons path that can never equal one. That case is answered per project rather
# than per machine: an absolute `repo_path` on the repo-map.json entry overrides
# $REPOS_DIR/$repo, and a project carrying one never reaches this script at all.
# Failing here is therefore not fatal to a lookup — callers treat it as "no tree
# on this machine" and fall back to whatever the map recorded.
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

cat >&2 <<'MSG'
could not resolve the repos tree (no candidate has >= 2 git repos); set REPOS_DIR explicitly.
If the checkout is not under a flat repos tree at all — a bind mount whose folder
name cannot match the repo field, as in the Odoo devcontainer — record the path on
the project instead, which bypasses this resolver entirely:
  repo-map.sh add "<project>" <repo> --repo-path /abs/path/to/checkout
MSG
exit 1
