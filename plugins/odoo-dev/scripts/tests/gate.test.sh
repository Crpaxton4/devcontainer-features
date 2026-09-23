#!/usr/bin/env bash
# gate.test.sh — one fixture per blocker, each asserting the exact blocker string.
#
# Offline: no network, no docker, no Odoo, no repos tree. If this passes, gate.sh
# blocks every way it is supposed to and passes every way it should.
#
# A blocker asserted by name rather than by exit code, because "exits 1" would
# stay green if the gate started blocking for the wrong reason.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATE="$HERE/../gate.sh"
ART="$HERE/../artifact.sh"
SD="$HERE/../state-dir.sh"
FIX="$HERE/fixtures/gate"

pass=0; fail=0
ok()   { echo "PASS $*"; pass=$((pass + 1)); }
bad()  { echo "FAIL $*"; fail=$((fail + 1)); }

# expect_block_at <dir> <label> <for> <blocker>
# Takes any artifacts dir, so a case built in a temp dir is asserted exactly the
# way a fixture is, and reads the same in the output.
expect_block_at() {
  local dir="$1" fx="$2" mode="$3" want="$4" out rc
  out="$(bash "$GATE" "$dir" --for "$mode" 2>/dev/null)"; rc=$?
  if [ "$rc" -ne 1 ]; then bad "$fx: expected exit 1, got $rc"; return; fi
  if ! printf '%s' "$out" | grep -q "\"blocker\":\"$want\""; then
    bad "$fx: expected blocker $want, got: $out"; return
  fi
  ok "$fx -> $want"
}

# expect_pass_at <dir> <label> <for>
expect_pass_at() {
  local dir="$1" fx="$2" mode="$3" out rc
  out="$(bash "$GATE" "$dir" --for "$mode" 2>/dev/null)"; rc=$?
  if [ "$rc" -ne 0 ]; then bad "$fx: expected exit 0, got $rc: $out"; return; fi
  if ! printf '%s' "$out" | grep -q '"ok":true'; then bad "$fx: ok!=true: $out"; return; fi
  ok "$fx -> clean"
}

# expect_warn_at <dir> <label> <for> <warning> <detail substring>
# A warning must pass and be reported. Asserting the detail too, because a warning
# whose text does not name the pull requests is not something a human can act on.
expect_warn_at() {
  local dir="$1" fx="$2" mode="$3" want="$4" detail="$5" out rc
  out="$(bash "$GATE" "$dir" --for "$mode" 2>/dev/null)"; rc=$?
  if [ "$rc" -ne 0 ]; then bad "$fx: expected exit 0, got $rc: $out"; return; fi
  if ! printf '%s' "$out" | grep -q '"ok":true'; then bad "$fx: ok!=true: $out"; return; fi
  if ! printf '%s' "$out" | grep -q "\"warning\":\"$want\""; then
    bad "$fx: expected warning $want, got: $out"; return
  fi
  if ! printf '%s' "$out" | grep -qF "$detail"; then
    bad "$fx: warning did not mention \"$detail\": $out"; return
  fi
  # A warning that also blocks is the bug this whole channel exists to prevent.
  if ! printf '%s' "$out" | grep -q '"blockers":\[\]'; then
    bad "$fx: warning $want also produced a blocker: $out"; return
  fi
  ok "$fx -> warns $want ($detail)"
}

# expect_block <fixture> <for> <blocker>
expect_block() { expect_block_at "$FIX/$1" "$1" "$2" "$3"; }

# expect_warn <fixture> <for> <warning> <detail substring>
expect_warn() { expect_warn_at "$FIX/$1" "$1" "$2" "$3" "$4"; }

# expect_pass <fixture> <for>
expect_pass() { expect_pass_at "$FIX/$1" "$1" "$2"; }

expect_pass  all-green                pr
expect_block zero-tests               pr      no_tests
expect_block tours-declared-none-run  pr      tours_skipped
expect_block failing-test             pr      tests_failed
expect_block incomplete-coderabbit    pr      review_incomplete
expect_block findings-unwaived        pr      review_incomplete
expect_pass  findings-waived          pr
expect_block worktree-drift           pr      worktree_drift
expect_pass  release-green            release
expect_block unconfirmed-flow         release unconfirmed_flow

# untagged_pr warns and ships. A merged PR with no task is the ordinary case, not
# an exception, and a release carrying one has to be able to complete. The warning
# names the pull requests because that is what a human acts on.
expect_warn  untagged-pr              release untagged_pr "#41"
expect_warn  untagged-and-inferred    release untagged_pr "#52, #80"
expect_warn_at "$FIX/untagged-and-inferred" "untagged-and-inferred (inferred id)" \
  release untagged_pr "not tagged: 30791"

# The manifest missing entirely is a different thing and still blocks: there is
# nothing to report and nothing to ship.
NOMANIFEST="$(mktemp -d)"
cp "$FIX/untagged-pr/00-context.json" "$NOMANIFEST/"
expect_block_at "$NOMANIFEST" "release with no 60-release.json" release untagged_pr

# An empty artifacts dir must fail closed on every delivery gate, not pass for
# lack of anything to object to.
EMPTY="$(mktemp -d)"
out="$(bash "$GATE" "$EMPTY" --for pr 2>/dev/null)"; rc=$?
if [ "$rc" -eq 1 ] \
   && printf '%s' "$out" | grep -q '"blocker":"no_tests"' \
   && printf '%s' "$out" | grep -q '"blocker":"review_incomplete"' \
   && printf '%s' "$out" | grep -q '"blocker":"worktree_drift"'; then
  ok "empty dir -> fails closed"
else
  bad "empty dir: rc=$rc out=$out"
fi

# A green retry after a red run must still pass, and must announce the revision
# count so "green on the second try" cannot read as "green on the first".
RETRY="$(mktemp -d)"
cp "$FIX/all-green/10-env.json" "$FIX/all-green/20-build.json" \
   "$FIX/all-green/40-coderabbit.json" "$RETRY/"
echo '{"passed":false,"tests_run":12,"tours_declared":0,"tours_run":0,"failures":[{"test":"t","error":"AssertionError: 2 != 1"}],"log_file":"/tmp/a.log"}' \
  | bash "$ART" put "$RETRY" 30-test - >/dev/null
echo '{"passed":true,"tests_run":12,"tours_declared":0,"tours_run":0,"failures":[],"log_file":"/tmp/b.log"}' \
  | bash "$ART" put "$RETRY" 30-test - >/dev/null
out="$(bash "$GATE" "$RETRY" --for pr 2>/dev/null)"; rc=$?
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q '"30-test":2'; then
  ok "red-then-green retry -> passes, revision count reported"
else
  bad "retry: rc=$rc out=$out"
fi

# artifact.sh must refuse to name a malformed payload as a stage.
MAL="$(mktemp -d)"
if ! echo '{"passed":true}' | bash "$ART" put "$MAL" 30-test - >/dev/null 2>&1 \
   && [ ! -f "$MAL/30-test.json" ]; then
  ok "artifact.sh rejects an incomplete 30-test payload"
else
  bad "artifact.sh accepted an incomplete 30-test payload"
fi

# --- waivers -------------------------------------------------------------------
# A CodeRabbit finding clears only through a matching entry in 45-waiver.json, and
# one entry is consumed per finding. These cases vary nothing but the review, so a
# blocker that fires can only have come from it.
WAIVER_TMP=()

# mk_review <coderabbit json> [waiver json] -> prints a fresh artifacts dir
mk_review() {
  local d; d="$(mktemp -d)"
  cp "$FIX/findings-unwaived/10-env.json" "$FIX/findings-unwaived/20-build.json" \
     "$FIX/findings-unwaived/30-test.json" "$d/"
  printf '%s\n' "$1" > "$d/40-coderabbit.json"
  if [ $# -ge 2 ]; then printf '%s\n' "$2" > "$d/45-waiver.json"; fi
  echo "$d"
}

SAME_FILE_TWICE='{"clean":false,"findings_count":2,"findings":[{"severity":"warning","file":"models/sale_order.py","comment":"sudo() without a record rule"},{"severity":"info","file":"models/sale_order.py","comment":"search inside a loop"}],"status":"complete","base":"staging"}'
ONE_WAIVER='{"waived":[{"file":"models/sale_order.py","line":null,"reason":"scheduled job, no user context, company already narrows the domain"}]}'
TWO_WAIVERS='{"waived":[{"file":"models/sale_order.py","line":null,"reason":"scheduled job, no user context, company already narrows the domain"},{"file":"models/sale_order.py","line":null,"reason":"the loop runs over at most one order per run"}]}'
FULL_WAIVERS='{"waived":[{"file":"models/sale_order.py","line":null,"reason":"scheduled job, no user context"},{"file":"views/sale_order_views.xml","line":null,"reason":"the field is meant to be visible to every sales user"}]}'
WRONG_FILE='{"waived":[{"file":"models/other_model.py","line":null,"reason":"waives a finding nobody reported"},{"file":"models/other_model.py","line":null,"reason":"waives a finding nobody reported"}]}'
RABBIT_UNWAIVED="$(cat "$FIX/findings-unwaived/40-coderabbit.json")"
RABBIT_TIMEOUT="${RABBIT_UNWAIVED/\"status\":\"complete\"/\"status\":\"timeout\"}"
COUNT_LIES='{"clean":true,"findings_count":0,"findings":[{"severity":"warning","file":"models/sale_order.py","comment":"sudo() without a record rule"}],"status":"complete","base":"staging"}'

d="$(mk_review "$SAME_FILE_TWICE" "$ONE_WAIVER")"; WAIVER_TMP+=("$d")
expect_block_at "$d" "one waiver, two findings in one file" pr review_incomplete

d="$(mk_review "$SAME_FILE_TWICE" "$TWO_WAIVERS")"; WAIVER_TMP+=("$d")
expect_pass_at "$d" "two waivers, two findings in one file" pr

d="$(mk_review "$RABBIT_UNWAIVED" "$WRONG_FILE")"; WAIVER_TMP+=("$d")
expect_block_at "$d" "waiver names a file nobody flagged" pr review_incomplete

d="$(mk_review "$RABBIT_TIMEOUT" "$FULL_WAIVERS")"; WAIVER_TMP+=("$d")
expect_block_at "$d" "timed-out review, every finding waived" pr review_incomplete

d="$(mk_review "$COUNT_LIES")"; WAIVER_TMP+=("$d")
expect_block_at "$d" "findings_count=0 with a non-empty findings[]" pr review_incomplete

# The latest waiver revision is the one that counts: the first put covers one of
# the two findings, the second covers both.
d="$(mk_review "$RABBIT_UNWAIVED")"; WAIVER_TMP+=("$d")
printf '%s' "$ONE_WAIVER"   | bash "$ART" put "$d" 45-waiver - >/dev/null
printf '%s' "$FULL_WAIVERS" | bash "$ART" put "$d" 45-waiver - >/dev/null
out="$(bash "$GATE" "$d" --for pr 2>/dev/null)"; rc=$?
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q '"45-waiver":2'; then
  ok "second waiver revision completes the coverage, revision count reported"
else
  bad "waiver revision: rc=$rc out=$out"
fi

# --- artifact.sh: the 45-waiver schema ------------------------------------------
# A waiver whose reason is empty is not an auditable "won't fix", so the writer
# refuses it rather than the gate discovering it later.

# expect_waiver_reject <label> <payload>
expect_waiver_reject() {
  local label="$1" payload="$2" d rc
  d="$(mktemp -d)"
  printf '%s' "$payload" | bash "$ART" put "$d" 45-waiver - >/dev/null 2>&1; rc=$?
  if [ "$rc" -eq 4 ] && [ ! -f "$d/45-waiver.json" ]; then
    ok "artifact.sh rejects a 45-waiver with $label"
  else
    bad "artifact.sh accepted a 45-waiver with $label (rc=$rc)"
  fi
  rm -rf "$d"
}

WAIVE_OK="$(mktemp -d)"; WAIVER_TMP+=("$WAIVE_OK")
if printf '%s' "$FULL_WAIVERS" | bash "$ART" put "$WAIVE_OK" 45-waiver - >/dev/null 2>&1 \
   && [ -f "$WAIVE_OK/45-waiver.json" ]; then
  ok "artifact.sh accepts a well-formed 45-waiver payload"
else
  bad "artifact.sh rejected a well-formed 45-waiver payload"
fi

expect_waiver_reject "an empty reason" \
  '{"waived":[{"file":"models/sale_order.py","line":null,"reason":"   "}]}'
expect_waiver_reject "no file" \
  '{"waived":[{"line":null,"reason":"a reason with nothing to attach it to"}]}'
expect_waiver_reject "a line that is neither a number nor null" \
  '{"waived":[{"file":"models/sale_order.py","line":"12","reason":"a line given as a string"}]}'

# --- state-dir.sh: bare task ids and the fail-soft CLI ---------------------------
# The commands used to open with an inline mkdir/grep/&&/|| preamble that a
# worktree-isolated session refuses to run, which made /odoo-dev:pr unreachable from
# the one place odoo-dev-builder is documented to work. The shell moved in here, so
# these cases are what stops it moving back out.

STATE="$(mktemp -d)"
mkdir -p "$STATE/tasks"
cp -r "$FIX/all-green" "$STATE/tasks/30412"
mkdir -p "$STATE/tasks/30413"

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

# --- gate.sh: bare task ids and --soft -------------------------------------------
# gate.sh takes the same argument the same way, so /odoo-dev:pr can pre-gate with a
# single unconditional call instead of a chain the worktree guard refuses.
out="$(env ODOO_DEV_STATE_DIR="$STATE" bash "$GATE" --for pr -- 30412 2>/dev/null)"; rc=$?
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q '"ok":true'; then
  ok "gate.sh: a bare task id resolves against the state dir"
else
  bad "gate.sh: bare task id: rc=$rc out=$out"
fi

# Options before the directory and options after it are the same invocation.
out="$(bash "$GATE" --for release -- "$FIX/release-green" 2>/dev/null)"; rc=$?
[ "$rc" -eq 0 ] && ok "gate.sh: options may precede the directory" \
  || bad "gate.sh: leading options: rc=$rc out=$out"

# --soft must never exit non-zero — a non-zero exit inside !`…` aborts the whole
# command expansion — and a red verdict must still arrive with its blockers, because
# the command body reads the JSON line and the marker line separately.
out="$(env ODOO_DEV_STATE_DIR="$STATE" bash "$GATE" --for pr --soft -- 30413 2>/dev/null)"; rc=$?
if [ "$rc" -eq 0 ] \
   && printf '%s' "$out" | grep -q '"blocker":"no_tests"' \
   && printf '%s' "$out" | grep -qx 'NO PASSING --for pr GATE'; then
  ok "gate.sh --soft: a red verdict exits 0 with its blockers and the marker"
else
  bad "gate.sh --soft red: rc=$rc out=$out"
fi

# A pass under --soft must NOT carry the marker, or the body reads every run as red.
out="$(env ODOO_DEV_STATE_DIR="$STATE" bash "$GATE" --for pr --soft -- 30412 2>/dev/null)"; rc=$?
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q '"ok":true' \
   && ! printf '%s' "$out" | grep -q 'NO PASSING'; then
  ok "gate.sh --soft: a clean verdict carries no marker"
else
  bad "gate.sh --soft clean: rc=$rc out=$out"
fi

# The release route hits the same injection with an argument that is not a task id,
# and an unusable argument under --soft is a marker, never a shell error.
for arg in '' 'Create' 'release' '../../etc'; do
  out="$(env ODOO_DEV_STATE_DIR="$STATE" bash "$GATE" --for pr --soft -- "$arg" 2>/dev/null)"; rc=$?
  if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -qx 'NO PASSING --for pr GATE'; then
    ok "gate.sh --soft: exits 0 with the marker on [$arg]"
  else
    bad "gate.sh --soft on [$arg]: rc=$rc out=$out"
  fi
done

# Even a usage error, which is the one thing --soft cannot be allowed to turn into a
# non-zero exit either.
out="$(env ODOO_DEV_STATE_DIR="$STATE" bash "$GATE" --soft --for bogus -- 30412 2>/dev/null)"; rc=$?
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -qx 'NO PASSING --for pr GATE'; then
  ok "gate.sh --soft: a usage error is a marker, not an aborted expansion"
else
  bad "gate.sh --soft usage: rc=$rc out=$out"
fi

# Without --soft the exit codes are exactly what gate-hook.sh reads: 1 is a verdict,
# everything else is "could not run" and denies.
env ODOO_DEV_STATE_DIR="$STATE" bash "$GATE" --for pr -- 30413 >/dev/null 2>&1; rc=$?
[ "$rc" -eq 1 ] && ok "gate.sh: blockers still exit 1 without --soft" \
  || bad "gate.sh: blockers exited $rc, wanted 1"

env ODOO_DEV_STATE_DIR="$STATE" bash "$GATE" --for pr -- 999 >/dev/null 2>&1; rc=$?
[ "$rc" -eq 2 ] && ok "gate.sh: an unresolvable dir is still usage, exit 2" \
  || bad "gate.sh: unresolvable dir exited $rc, wanted 2"

bash "$GATE" >/dev/null 2>&1; rc=$?
[ "$rc" -eq 2 ] && ok "gate.sh: no argument is still usage, exit 2" \
  || bad "gate.sh: no argument exited $rc, wanted 2"

# artifact.sh takes the same argument the same way, so a chain never has to spell the
# state dir out twice.
if env ODOO_DEV_STATE_DIR="$STATE" bash "$ART" list 30412 2>/dev/null \
     | grep -q "\"dir\":\"$STATE/tasks/30412\""; then
  ok "artifact.sh: a bare task id resolves against the state dir"
else
  bad "artifact.sh: bare task id did not resolve"
fi

rm -rf "$EMPTY" "$RETRY" "$MAL" "$NOMANIFEST" "$STATE" "${WAIVER_TMP[@]}"

echo
echo "gate.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
