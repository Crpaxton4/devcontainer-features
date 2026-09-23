#!/usr/bin/env bash
# repos-dir.test.sh — offline unit tests for repos-dir.sh.
# No network, no real repos tree, no Odoo, no git: a repo is any directory
# holding a .git entry, which is exactly what the resolver tests for.
#
# Every case pins both REPOS_DIR and REPOS_DIR_CANDIDATES and runs from a
# controlled $PWD, because $PWD is the first candidate swept — a test that let
# it default would pass or fail depending on where it was invoked from.
#
# Usage: bash scripts/tests/repos-dir.test.sh   (exit 0 = all pass)
set -uo pipefail

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0; fail=0
check() { # check <name> <expected_exit> <actual_exit>
  if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: expected exit $2, got $3"; fi
}
check_eq() { # check_eq <name> <expected> <actual>
  if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: expected '$2', got '$3'"; fi
}
check_contains() { # check_contains <name> <needle> <haystack>
  case "$3" in *"$2"*) pass=$((pass+1)) ;; *) fail=$((fail+1)); echo "FAIL $1: '$2' not in output: $3" ;; esac
}

mkrepo() { mkdir -p "$1/.git"; }         # a git repo, as the resolver defines one
mark()   { : > "$1/.odoo-repos-dir"; }   # the single-repo tree opt-in

# run <cwd> <candidates> [args...] — REPOS_DIR deliberately empty, which the
# resolver treats as unset, so these exercise the sweep and nothing else.
run() {
  local cwd="$1" cands="$2"; shift 2
  (cd "$cwd" && REPOS_DIR= REPOS_DIR_CANDIDATES="$cands" bash "$SCRIPTS/repos-dir.sh" "$@")
}
# run_set <repos_dir> [args...] — the explicit path, which must never sweep.
run_set() {
  local rd="$1"; shift
  (cd "$TMP" && REPOS_DIR="$rd" REPOS_DIR_CANDIDATES="$TMP/none" bash "$SCRIPTS/repos-dir.sh" "$@")
}

NONE="$TMP/none"          # a candidate list that resolves nothing
mkdir -p "$TMP/neutral"   # a $PWD that is not a tree under any rule

# --- fixtures -----------------------------------------------------------------
mkdir -p "$TMP/two" "$TMP/one" "$TMP/one-marked" "$TMP/empty-marked" "$TMP/bare"
mkrepo "$TMP/two/alpha"; mkrepo "$TMP/two/beta"
mkrepo "$TMP/one/alpha"
mkrepo "$TMP/one-marked/alpha"; mark "$TMP/one-marked"
mark "$TMP/empty-marked"

# --- explicit REPOS_DIR: a declaration, never counted -------------------------
# The bug report claimed an explicit REPOS_DIR holding one clone exits 1. It does
# not, and never did: the explicit branch returns before the sweep. These pin that
# so a future threshold change cannot quietly acquire the behaviour.
out="$(run_set "$TMP/one" --raw 2>&1)"; check "explicit-one-repo" 0 $?
check_eq "explicit-one-repo-path" "$TMP/one" "$out"

out="$(run_set "$TMP/bare" --raw 2>&1)"; check "explicit-zero-repos" 0 $?
check_eq "explicit-zero-repos-path" "$TMP/bare" "$out"

out="$(run_set "$TMP/nope" --raw 2>&1)"; check "explicit-missing-dir" 1 $?
check_contains "explicit-missing-msg" "REPOS_DIR is set but not a directory" "$out"

# A file is not a tree, however explicit the operator was.
: > "$TMP/afile"
out="$(run_set "$TMP/afile" --raw 2>&1)"; check "explicit-not-a-dir" 1 $?

# --- sweep: the >= 2 heuristic still holds ------------------------------------
out="$(run "$TMP/neutral" "$TMP/two" --raw 2>&1)"; check "sweep-two-repos" 0 $?
check_eq "sweep-two-repos-path" "$TMP/two" "$out"

# The false negative this issue is about: one clone, nothing declared.
out="$(run "$TMP/neutral" "$TMP/one" --raw 2>&1)"; check "sweep-one-repo-unmarked" 1 $?
check_contains "sweep-one-repo-msg" ".odoo-repos-dir marker" "$out"

# --- sweep: the marker is the single-repo opt-in ------------------------------
out="$(run "$TMP/neutral" "$TMP/one-marked" --raw 2>&1)"; check "sweep-one-repo-marked" 0 $?
check_eq "sweep-one-repo-marked-path" "$TMP/one-marked" "$out"

# A declared tree resolves before anything is cloned into it — the state a fresh
# host starts in, and the reason the marker outranks the count instead of
# lowering it to one.
out="$(run "$TMP/neutral" "$TMP/empty-marked" --raw 2>&1)"; check "sweep-empty-marked" 0 $?
check_eq "sweep-empty-marked-path" "$TMP/empty-marked" "$out"

# The marker cannot conjure a directory that is not there.
out="$(run "$TMP/neutral" "$TMP/absent-marked" --raw 2>&1)"; check "sweep-marker-missing-dir" 1 $?

# --- the false positive the threshold exists to prevent -----------------------
# $PWD is swept first, so an unmarked working directory holding one checkout must
# not be mistaken for the tree. This is what a naive relaxation to >= 1 would
# have broken, and it is the whole reason the fix is a marker.
mkdir -p "$TMP/cwd-one"; mkrepo "$TMP/cwd-one/alpha"
out="$(run "$TMP/cwd-one" "$NONE" --raw 2>&1)"; check "pwd-one-repo-not-a-tree" 1 $?

# ... but a marked $PWD is, and it still beats the candidate list.
mark "$TMP/cwd-one"
out="$(run "$TMP/cwd-one" "$TMP/two" --raw 2>&1)"; check "pwd-marked-wins" 0 $?
check_eq "pwd-marked-wins-path" "$TMP/cwd-one" "$out"

# An unmarked $PWD with two repos also wins — ordering is unchanged by this fix.
out="$(run "$TMP/two" "$TMP/one-marked" --raw 2>&1)"; check "pwd-two-repos-wins" 0 $?
check_eq "pwd-two-repos-wins-path" "$TMP/two" "$out"

# --- output contract ----------------------------------------------------------
out="$(run "$TMP/neutral" "$TMP/two" 2>&1)"; check "json-mode" 0 $?
check_eq "json-mode-shape" "{\"repos_dir\": \"$TMP/two\"}" "$out"

out="$(run "$TMP/neutral" "$NONE" --raw 2>&1)"; check "unresolvable-exit" 1 $?
check_contains "unresolvable-mentions-repos-dir" "set REPOS_DIR explicitly" "$out"
check_contains "unresolvable-mentions-repo-path" "--repo-path" "$out"

out="$(run "$TMP/neutral" "$TMP/two" --bogus 2>&1)"; check "bad-flag" 2 $?
check_contains "bad-flag-usage" "usage: repos-dir.sh" "$out"

# Empty entries in the candidate list are skipped, not resolved to the cwd.
out="$(run "$TMP/neutral" "::$TMP/two:" --raw 2>&1)"; check "empty-candidates-skipped" 0 $?
check_eq "empty-candidates-skipped-path" "$TMP/two" "$out"

echo "{\"passed\": $pass, \"failed\": $fail}"
[ "$fail" -eq 0 ]
