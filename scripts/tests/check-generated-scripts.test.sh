#!/usr/bin/env bash
# check-generated-scripts.test.sh — offline tests for check-generated-scripts.sh.
#
# No network, no docker, no container build: every case is a hand-written fake
# install.sh under a throwaway dir, so the real Feature install.sh is never a
# test input and a sibling PR editing it can never turn these red.
#
# Each fixture is a whole, VALID bash file — that is the point of #872. The
# breakage always lives inside a quoted heredoc, where install.sh's own parser
# cannot see it, so every fixture below passes `bash -n` and only the extracted
# artifact tells the truth.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$HERE/../check-generated-scripts.sh"
work="$(mktemp -d "${TMPDIR:-/tmp}/check-generated-scripts-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0
fail=0
ok() {
    echo "PASS $*"
    pass=$((pass + 1))
}
bad() {
    echo "FAIL $*"
    fail=$((fail + 1))
}

run() {  # run <fixture> -> sets $out and $rc
    out="$(bash "$SUT" "$work/$1" 2>&1)"
    rc=$?
}

expect_rc() {  # expect_rc <case> <wanted>
    if [ "$rc" -eq "$2" ]; then
        ok "$1: exit $2"
    else
        bad "$1: exit $rc, wanted $2 — output: $out"
    fi
}

expect_match() {  # expect_match <case> <regex>
    if printf '%s\n' "$out" | grep -Eq -- "$2"; then
        ok "$1: output matches /$2/"
    else
        bad "$1: output does not match /$2/ — output: $out"
    fi
}

expect_bash_n_clean() {  # the fixture itself must be valid bash, or it proves nothing
    if bash -n "$work/$1" 2>/dev/null; then
        ok "$1: the fixture is itself valid bash (bash -n cannot see the bug)"
    else
        bad "$1: the fixture does not pass bash -n, so it is not a #872 fixture"
    fi
}

# --- a good block passes -------------------------------------------------------
# Exercises all three shapes at once: a sound POSIX body, a sound `python3 -c`
# program that legitimately contains an apostrophe via the '\'' idiom, and a
# sound `python3 - <<'DELIM'` heredoc.

cat > "$work/good.sh" <<'FIXTURE_GOOD'
#!/usr/bin/env bash
set -euo pipefail

cat > /usr/local/bin/good-tool << 'GOOD_TOOL'
#!/bin/sh
set -e

if [ "${1:-}" = "--version" ]; then
    echo "1.0"
    exit 0
fi

python3 -c '
# the palace'\''s config file is read here, apostrophe and all
import json, sys
print(json.dumps({"ok": True}))
'

python3 - <<'GOOD_TOOL_PY'
import os
print(os.environ.get("HOME", ""))
GOOD_TOOL_PY
GOOD_TOOL
chmod 0755 /usr/local/bin/good-tool
FIXTURE_GOOD

run good.sh
expect_bash_n_clean good.sh
expect_rc "good" 0
expect_match "good" '^good-tool: ok$'
expect_match "good" '2 embedded python program\(s\) ok'

# --- a shell syntax error in a generated block fails, naming the block ----------
# `if` with no `fi`. Inert to install.sh's parser; fatal to the shipped script.

cat > "$work/broken-shell.sh" <<'FIXTURE_SHELL'
#!/usr/bin/env bash
set -euo pipefail

cat > /usr/local/bin/broken-tool << 'BROKEN_TOOL'
#!/bin/sh
set -e

if [ -z "${HOME:-}" ]; then
    echo "no home" >&2
BROKEN_TOOL
chmod 0755 /usr/local/bin/broken-tool
FIXTURE_SHELL

run broken-shell.sh
expect_bash_n_clean broken-shell.sh
expect_rc "broken-shell" 1
expect_match "broken-shell" 'broken-tool: sh -n failed'
# and it must NOT claim the block is fine
if printf '%s\n' "$out" | grep -q '^broken-tool: ok$'; then
    bad "broken-shell: reported the broken block as ok"
else
    ok "broken-shell: did not report the broken block as ok"
fi

# --- an apostrophe in a python3 -c program fails, naming the line --------------
# This is #872 itself: an ordinary English apostrophe in a Python COMMENT. The
# fixture is valid bash and the comment looks harmless at the point of editing.

cat > "$work/apostrophe.sh" <<'FIXTURE_APOSTROPHE'
#!/usr/bin/env bash
set -euo pipefail

cat > /usr/local/bin/apostrophe-tool << 'APOSTROPHE_TOOL'
#!/bin/sh
set -e

python3 -c '
# this script's private marker file is written below
import json
print(json.dumps({"marker": 1}))
'
APOSTROPHE_TOOL
chmod 0755 /usr/local/bin/apostrophe-tool
FIXTURE_APOSTROPHE

run apostrophe.sh
expect_bash_n_clean apostrophe.sh
expect_rc "apostrophe" 1
# line 9 of the fixture is the comment carrying the apostrophe
expect_match "apostrophe" "apostrophe.sh: apostrophe-tool:9: python3 -c: the single-quoted program ends at an apostrophe"
expect_match "apostrophe" '#872'

# --- a python3 heredoc with a syntax error fails ------------------------------
# The inverse error: valid shell, invalid Python. Placed at install.sh top level
# rather than inside a generated block, because install.sh runs Python directly
# too and that half must be swept as well.

cat > "$work/broken-python.sh" <<'FIXTURE_PYTHON'
#!/usr/bin/env bash
set -euo pipefail

cat > /usr/local/bin/fine-tool << 'FINE_TOOL'
#!/bin/sh
echo fine
FINE_TOOL

python3 - <<'BROKEN_PY'
import os


def broken(:
    return os.getcwd()
BROKEN_PY
FIXTURE_PYTHON

run broken-python.sh
expect_bash_n_clean broken-python.sh
expect_rc "broken-python" 1
# the bad def is line 13 of the fixture
expect_match "broken-python" 'broken-python\.sh:13: python3 <<BROKEN_PY:'

# --- a file that generates nothing is itself a finding ------------------------
# A green gate that checked zero scripts is how #872 shipped in the first place,
# so silence is not success unless the caller asked for it.

cat > "$work/no-blocks.sh" <<'FIXTURE_EMPTY'
#!/usr/bin/env bash
set -euo pipefail
echo "nothing generated here"
FIXTURE_EMPTY

run no-blocks.sh
expect_rc "no-blocks" 1
expect_match "no-blocks" 'no generated scripts were found'

out="$(bash "$SUT" --allow-no-blocks "$work/no-blocks.sh" 2>&1)"
rc=$?
expect_rc "no-blocks --allow-no-blocks" 0

# --- --help is exit 0 and says how to call it ---------------------------------

out="$(bash "$SUT" --help 2>&1)"
rc=$?
expect_rc "--help" 0
expect_match "--help" 'usage: check-generated-scripts\.sh'

echo
echo "passed: $pass  failed: $fail"
[ "$fail" -eq 0 ]
