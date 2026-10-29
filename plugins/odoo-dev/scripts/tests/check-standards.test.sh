#!/usr/bin/env bash
# check-standards.test.sh — offline tests for check-standards.sh.
#
# Real throwaway git repositories with a base commit and a head commit. No gh, no
# network, no Odoo. Each rule has one fixture that breaks it and nothing else, and
# the test asserts the rule name appears exactly once. The clean fixture holds the
# allowed shapes (for in for, if in for, an elif chain) and must exit 0.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SUT="$ROOT/scripts/check-standards.sh"
FIX="$SCRIPT_DIR/fixtures/check-standards"
work="$(mktemp -d "${TMPDIR:-/tmp}/check-standards-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
expect() { if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted '$3', got '$2'" >&2; fi; }

repo="$work/repo"; mkdir -p "$work/no-hooks"
git init --quiet -b main "$repo"
git -C "$repo" config user.email t@e.invalid
git -C "$repo" config user.name t
git -C "$repo" config core.hooksPath "$work/no-hooks"
mkdir -p "$repo/mod/models"
# A legacy file that breaks every rule. It is in the base, so it is never checked.
cp "$FIX/nested_if.py" "$repo/mod/models/legacy.py"
git -C "$repo" add -A
git -C "$repo" commit --quiet -m base

# head_with <branch> <fixture>: a branch off main that adds the fixture as mod/models/new.py.
head_with() {
  git -C "$repo" checkout --quiet -b "$1" main
  cp "$FIX/$2" "$repo/mod/models/new.py"
  git -C "$repo" add -A
  git -C "$repo" commit --quiet -m "head $1"
}

# rule_case <fixture> <rule>: exit 1 and exactly one line carrying the rule.
rule_case() {
  head_with "b-$1" "$1"
  out="$("$SUT" "$repo" main 2>/dev/null)"; rc=$?
  expect "$2 exit" "$rc" 1
  expect "$2 count" "$(printf '%s\n' "$out" | grep -c " $2 ")" 1
  expect "$2 names the file" "$(printf '%s\n' "$out" | grep -c '^mod/models/new.py:[0-9]* ')" 1
}

head_with b-clean clean.py
out="$("$SUT" "$repo" main 2>&1)"; rc=$?
expect "clean exit" "$rc" 0
expect "clean output" "$out" ""

rule_case two_models.py one-model-per-file
rule_case no_docstring.py docstring
rule_case nested_if.py nesting
rule_case nested_deep.py nesting
rule_case long_function.py max-loc
rule_case complex.py complexity

# Empty base: warns and measures against origin/HEAD.
git -C "$repo" update-ref refs/remotes/origin/HEAD main
git -C "$repo" checkout --quiet b-complex.py
out="$("$SUT" "$repo" "" 2>"$work/empty.err")"; rc=$?
expect "empty base exit" "$rc" 1
expect "empty base warns" "$(grep -c 'WARN base_branch is empty' "$work/empty.err")" 1
expect "empty base finds" "$(printf '%s\n' "$out" | grep -c ' complexity ')" 1

# Missing worktree and unknown base are usage errors.
"$SUT" "$work/nowhere" main >/dev/null 2>&1; expect "missing worktree exit" "$?" 2
"$SUT" "$repo" no-such-branch >/dev/null 2>&1; expect "unknown base exit" "$?" 2
"$SUT" >/dev/null 2>&1; expect "no args exit" "$?" 2

echo "check-standards.test.sh: $pass passed, $fail failed"
[ "$fail" = 0 ]
