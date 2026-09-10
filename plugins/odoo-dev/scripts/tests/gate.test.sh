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

rm -rf "$EMPTY" "$RETRY" "$MAL" "$NOMANIFEST" "${WAIVER_TMP[@]}"

echo
echo "gate.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
