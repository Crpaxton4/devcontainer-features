#!/usr/bin/env bash
# state-dir.test.sh — the state-dir CLI: bare task ids, release keys, fail-soft.
#
# Offline: no network, no docker, no Odoo, no repos tree. Everything is built under
# mktemp and torn down at the end.
#
# The commands used to open with an inline mkdir/grep/&&/|| preamble that a
# worktree-isolated session refuses to run, which made /odoo-dev:pr unreachable from
# the one place odoo-dev-builder is documented to work. The shell moved into
# state-dir.sh, so these cases are what stops it moving back out.
#
# These cases lived in gate.test.sh until the PR gate was removed (#904). They are
# about state-dir.sh and never were about the gate, so they moved here rather than
# going out with it.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SD="$HERE/../state-dir.sh"
ART="$HERE/../artifact.sh"

pass=0; fail=0
ok()  { echo "PASS $*"; pass=$((pass + 1)); }
bad() { echo "FAIL $*"; fail=$((fail + 1)); }

STATE="$(mktemp -d)"
mkdir -p "$STATE/tasks/30412" "$STATE/tasks/30413"

# sd <args...> — state-dir.sh against the scratch state dir. Prints "rc|output".
sd() {
  local out rc
  out="$(env ODOO_DEV_STATE_DIR="$STATE" bash "$SD" "$@" 2>/dev/null)"; rc=$?
  printf '%s|%s' "$rc" "$out"
}

# expect_sd <label> <want rc|output> <args...>
expect_sd() {
  local label="$1" want="$2"; shift 2
  local got; got="$(sd "$@")"
  if [ "$got" = "$want" ]; then ok "state-dir.sh: $label"
  else bad "state-dir.sh: $label — wanted [$want], got [$got]"; fi
}

expect_sd "resolves a bare task id"      "0|$STATE/tasks/30412" task --else M -- 30412
expect_sd "creates on demand"            "0|$STATE/tasks/777"   task --create --else M -- 777
expect_sd "will not invent a task dir"   "0|M"                  task --else M -- 999
expect_sd "prose is not a task id"       "0|M"                  task --create --else M -- Create
expect_sd "an empty argument is not one" "0|M"                  task --create --else M -- ''
expect_sd "a path is not a task id"      "0|M"                  task --create --else M -- /tmp
expect_sd "release route resolves"       "0|$STATE/releases/UAT-to-main" \
  release --create --else M -- release UAT main
expect_sd "release needs its sentinel"   "0|M" release --create --else M -- 30412 UAT main
expect_sd "release refuses traversal"    "0|M" release --create --else M -- release ../etc main
expect_sd "release refuses a dotted-out branch" "0|M" release --create --else M -- release a..b main
expect_sd "the state dir itself"         "0|$STATE"

# Nothing above may have created a directory a well-formed invocation could not ask
# for. `tasks/Create` is the bug this assertion is named after.
junk="$(find "$STATE" -mindepth 1 -maxdepth 2 -type d \
        -not -name tasks -not -name releases \
        -not -name '3041[23]' -not -name 777 -not -name UAT-to-main | sort)"
if [ -z "$junk" ]; then ok "state-dir.sh: created no directory a bad argument asked for"
else bad "state-dir.sh: junk under the state dir: $junk"; fi

# A malformed CLI call is a bug in the command file, not something a user typed, so
# it is the one thing that still exits non-zero.
env ODOO_DEV_STATE_DIR="$STATE" bash "$SD" bogus >/dev/null 2>&1; rc=$?
[ "$rc" -eq 2 ] && ok "state-dir.sh: an unknown mode is still a usage error" \
  || bad "state-dir.sh: unknown mode exited $rc, wanted 2"

# artifact.sh takes the same argument the same way, through the same sourced
# helpers, so a chain never has to spell the state dir out twice.
if env ODOO_DEV_STATE_DIR="$STATE" bash "$ART" list 30412 2>/dev/null \
     | grep -q "\"dir\":\"$STATE/tasks/30412\""; then
  ok "artifact.sh: a bare task id resolves against the state dir"
else
  bad "artifact.sh: bare task id did not resolve"
fi

rm -rf "$STATE"

echo
echo "state-dir.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
