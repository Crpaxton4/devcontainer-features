#!/usr/bin/env bash
# state-dir.test.sh — the state-dir CLI (bare task ids, fail-soft) and artifact.sh
# (one task.json per task, four sections).
#
# Offline: no network, no docker, no Odoo, no repos tree. Everything is built under
# mktemp and torn down at the end.
#
# The commands used to open with an inline mkdir/grep/&&/|| preamble that a
# worktree-isolated session refuses to run, which made /odoo-dev:pr unreachable from
# the one place odoo-dev-builder is documented to work. The shell moved into
# state-dir.sh, so these cases are what stops it moving back out.
#
# artifact.sh is asserted at the bottom, by running it for real: `set` replaces a
# section in place (whole, --key, --text), refuses an unknown section with 3 and an
# invalid payload with 4; `get` reads a section or a key, `--raw` unquotes a string;
# `list` and `sections` describe the file and the schema.
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
expect_sd "the route word is not one"    "0|M"                  task --create --else M -- release
expect_sd "the state dir itself"         "0|$STATE"

# Nothing above may have created a directory a well-formed invocation could not ask
# for. `tasks/Create` is the bug this assertion is named after.
junk="$(find "$STATE" -mindepth 1 -maxdepth 2 -type d \
        -not -name tasks -not -name '3041[23]' -not -name 777 | sort)"
if [ -z "$junk" ]; then ok "state-dir.sh: created no directory a bad argument asked for"
else bad "state-dir.sh: junk under the state dir: $junk"; fi

# A malformed CLI call is a bug in the command file, not something a user typed, so
# it is the one thing that still exits non-zero. The retired release mode is one.
env ODOO_DEV_STATE_DIR="$STATE" bash "$SD" bogus >/dev/null 2>&1; rc=$?
[ "$rc" -eq 2 ] && ok "state-dir.sh: an unknown mode is still a usage error" \
  || bad "state-dir.sh: unknown mode exited $rc, wanted 2"
env ODOO_DEV_STATE_DIR="$STATE" bash "$SD" release --create -- release UAT main >/dev/null 2>&1; rc=$?
[ "$rc" -eq 2 ] && ok "state-dir.sh: the release mode is gone" \
  || bad "state-dir.sh: release mode exited $rc, wanted 2"

# artifact.sh takes the same argument the same way, through the same sourced
# helpers, so a chain never has to spell the state dir out twice.
if env ODOO_DEV_STATE_DIR="$STATE" bash "$ART" list 30412 2>/dev/null \
     | grep -q "\"file\":\"$STATE/tasks/30412/task.json\""; then
  ok "artifact.sh: a bare task id resolves against the state dir"
else
  bad "artifact.sh: bare task id did not resolve"
fi

# --- artifact.sh: one file, four sections -----------------------------------------
A="$STATE/tasks/30413"

if [ "$(bash "$ART" sections | paste -sd, -)" = "plan,build,test,review" ]; then
  ok "artifact.sh: sections are exactly plan, build, test, review"
else
  bad "artifact.sh: sections printed $(bash "$ART" sections | paste -sd, -)"
fi

# A build section with its five keys lands in task.json.
BUILD='{"worktree":"/wt","branch":"task-30413","modules":["acme_sale"],"odoo_version":"18.0","default_branch":"staging"}'
out="$(bash "$ART" set "$A" build --json "$BUILD" 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -qF '"file":"'"$A"'/task.json"' \
   && [ -f "$A/task.json" ]; then
  ok "artifact.sh: set build writes task.json"
else
  bad "artifact.sh: set build: rc=$rc out=$out"
fi

# A second set of the same section REPLACES it; the file holds the latest state only.
BUILD2='{"worktree":"/wt","branch":"task-30413","modules":["acme_sale","acme_stock"],"odoo_version":"18.0","default_branch":"staging"}'
bash "$ART" set "$A" build --json "$BUILD2" >/dev/null 2>&1
n="$(bash "$ART" get "$A" build --key modules 2>/dev/null | grep -c acme_)"
[ "$n" = 2 ] && ok "artifact.sh: a second set replaces the section in place" \
  || bad "artifact.sh: second set did not replace: modules count $n"
files="$(find "$A" -maxdepth 1 -type f | wc -l | tr -d ' ')"
[ "$files" = 1 ] && ok "artifact.sh: one file per task, no revisions" \
  || bad "artifact.sh: wanted 1 file in $A, found $files"

# A missing required key exits 4 and names the key; nothing is written.
out="$(bash "$ART" set "$A" build --json '{"worktree":"/wt","branch":"b","modules":[]}' 2>&1)"; rc=$?
if [ "$rc" -eq 4 ] && printf '%s' "$out" | grep -qF 'missing required field: odoo_version'; then
  ok "artifact.sh: a build without its five keys exits 4 naming the key"
else
  bad "artifact.sh: incomplete build: rc=$rc out=$out"
fi
n="$(bash "$ART" get "$A" build --key modules 2>/dev/null | grep -c acme_)"
[ "$n" = 2 ] && ok "artifact.sh: a refused payload left the section untouched" \
  || bad "artifact.sh: refused payload changed the section"

# A wrong type exits 4 too.
out="$(bash "$ART" set "$A" build --json '{"worktree":"/wt","branch":"b","modules":"acme","odoo_version":"18.0","default_branch":"main"}' 2>&1)"; rc=$?
[ "$rc" -eq 4 ] && printf '%s' "$out" | grep -qF 'field modules: expected array' \
  && ok "artifact.sh: modules must be an array" \
  || bad "artifact.sh: wrong type: rc=$rc out=$out"

# An unknown section exits 3.
bash "$ART" set "$A" "60-release" --json '{}' >/dev/null 2>&1; rc=$?
[ "$rc" -eq 3 ] && ok "artifact.sh: an unknown section exits 3" \
  || bad "artifact.sh: unknown section exited $rc, wanted 3"

# Not JSON at all exits 4.
bash "$ART" set "$A" review --json 'not json' >/dev/null 2>&1; rc=$?
[ "$rc" -eq 4 ] && ok "artifact.sh: a non-JSON payload exits 4" \
  || bad "artifact.sh: non-JSON exited $rc, wanted 4"

# --key writes one entry of the keyed test section, from stdin, and leaves the others.
printf '%s' '{"passed":true,"tests_run":4,"produced_by":"run-tests.sh"}' \
  | bash "$ART" set "$A" test --key acme_sale - >/dev/null 2>&1; rc=$?
[ "$rc" -eq 0 ] && ok "artifact.sh: set test --key from stdin" \
  || bad "artifact.sh: set test --key exited $rc"
printf '%s' '{"passed":false,"tests_run":0,"produced_by":"run-tests.sh"}' \
  | bash "$ART" set "$A" test --key acme_stock - >/dev/null 2>&1
keys="$(bash "$ART" get "$A" test 2>/dev/null | grep -oE '"acme_[a-z]+"' | paste -sd, -)"
[ "$keys" = '"acme_sale","acme_stock"' ] && ok "artifact.sh: two --key writes keep both entries" \
  || bad "artifact.sh: keyed entries read back as $keys"
[ "$(bash "$ART" get "$A" test --key acme_sale --raw 2>/dev/null | grep -c '"passed": true')" = 1 ] \
  && ok "artifact.sh: get --key reads one entry" \
  || bad "artifact.sh: get --key acme_sale did not read back"

# --text stores the plan markdown; --raw prints it unquoted.
printf '# Plan\n\n## Acceptance criteria\n\n1. It works.\n' > "$STATE/plan.md"
bash "$ART" set "$A" plan --text "$STATE/plan.md" >/dev/null 2>&1; rc=$?
[ "$rc" -eq 0 ] && ok "artifact.sh: set plan --text" || bad "artifact.sh: set plan --text exited $rc"
if [ "$(bash "$ART" get "$A" plan --raw 2>/dev/null)" = "$(cat "$STATE/plan.md")" ]; then
  ok "artifact.sh: get plan --raw prints the markdown verbatim"
else
  bad "artifact.sh: get plan --raw did not round-trip"
fi
bash "$ART" get "$A" plan 2>/dev/null | grep -q '"markdown"' \
  && ok "artifact.sh: get plan without --raw prints the JSON" \
  || bad "artifact.sh: get plan JSON lacks the markdown key"

# A plan payload without markdown exits 4.
bash "$ART" set "$A" plan --json '{"steps":[]}' >/dev/null 2>&1; rc=$?
[ "$rc" -eq 4 ] && ok "artifact.sh: a plan without markdown exits 4" \
  || bad "artifact.sh: plan without markdown exited $rc, wanted 4"

# get on an absent section or key exits 5; get on an absent file exits 5.
bash "$ART" get "$A" review >/dev/null 2>&1; rc=$?
[ "$rc" -eq 5 ] && ok "artifact.sh: get of an absent section exits 5" \
  || bad "artifact.sh: absent section exited $rc, wanted 5"
bash "$ART" get "$A" build --key nope >/dev/null 2>&1; rc=$?
[ "$rc" -eq 5 ] && ok "artifact.sh: get of an absent key exits 5" \
  || bad "artifact.sh: absent key exited $rc, wanted 5"
bash "$ART" get "$STATE/tasks/30412" >/dev/null 2>&1; rc=$?
[ "$rc" -eq 5 ] && ok "artifact.sh: get with no task.json exits 5" \
  || bad "artifact.sh: absent file exited $rc, wanted 5"

# The review section takes any JSON object through the inline form (the tester's).
bash "$ART" set "$A" review --json '{"findings":[],"criteria_results":[{"criterion":"1","verdict":"pass"}]}' >/dev/null 2>&1; rc=$?
[ "$rc" -eq 0 ] && ok "artifact.sh: set review --json" || bad "artifact.sh: set review exited $rc"

# list names every section and its keys; get with no section prints the whole file.
out="$(bash "$ART" list "$A" 2>/dev/null)"
if printf '%s' "$out" | grep -qF '"name":"build"' && printf '%s' "$out" | grep -qF '"name":"test"' \
   && printf '%s' "$out" | grep -qF '"name":"plan"' && printf '%s' "$out" | grep -qF '"name":"review"' \
   && printf '%s' "$out" | grep -qF '"acme_stock"'; then
  ok "artifact.sh: list names the four sections and their keys"
else
  bad "artifact.sh: list printed $out"
fi
[ "$(bash "$ART" get "$A" 2>/dev/null | grep -cE '^  "(plan|build|test|review)":')" = 4 ] \
  && ok "artifact.sh: get with no section prints the whole file" \
  || bad "artifact.sh: whole-file get did not show four sections"
bash "$ART" list "$STATE/tasks/30412" 2>/dev/null | grep -qF '"sections":[]' \
  && ok "artifact.sh: list on an empty task dir reports no sections" \
  || bad "artifact.sh: list on an empty dir did not report []"

# No temp file is left behind by any of the above.
tmps="$(find "$A" -maxdepth 1 -name '.task.*.tmp' | wc -l | tr -d ' ')"
[ "$tmps" = 0 ] && ok "artifact.sh: atomic writes leave no temp file" \
  || bad "artifact.sh: $tmps temp file(s) left in $A"

rm -rf "$STATE"

echo
echo "state-dir.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
