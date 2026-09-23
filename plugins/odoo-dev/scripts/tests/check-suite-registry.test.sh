#!/usr/bin/env bash
# check-suite-registry.test.sh — offline tests for check-suite-registry.sh.
#
# No network, no docker, no SDK. Every case but the last builds a throwaway
# plugin tree and a throwaway validate.sh under --plugin-root / --validate, so
# the real registries are never read and a failure here is never a failure of
# the real tree.
#
# The recursion is deliberate and worth stating: this suite is registered in the
# very run_suite block the script under test reconciles. An unregistered
# reconciler would be the first thing its own check caught, and the opt-out list
# would need an exemption for the gate that polices exemptions.
#
# The last case is the only one that touches reality: it runs the script with
# its built-in opt-out list against the real plugin, which is what gate 21 does.
# That case is what turns the CI_ONLY list from prose into something checked.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$HERE/../check-suite-registry.sh"
PLUGIN="$(cd "$HERE/../.." && pwd)"
work="$(mktemp -d "${TMPDIR:-/tmp}/suite-registry-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
ok()  { echo "PASS $*"; pass=$((pass + 1)); }
bad() { echo "FAIL $*"; fail=$((fail + 1)); }

TAB=$'\t'

# make_tree <dir> <relpath>...  — a fake plugin root carrying those suite files
make_tree() {
  local dir="$1"; shift
  local rel
  mkdir -p "$dir/scripts"
  for rel in "$@"; do
    mkdir -p "$dir/$(dirname "$rel")"
    printf '#!/usr/bin/env bash\ntrue\n' > "$dir/$rel"
  done
}

# make_validate <dir> <relpath>...  — a fake validate.sh registering those paths,
# written in the same $HERE / $SKILLS shorthand the real one uses, because
# resolving that shorthand is half of what the script under test does.
make_validate() {
  local dir="$1"; shift
  local rel out="$dir/scripts/validate.sh"
  {
    echo '#!/usr/bin/env bash'
    echo 'run_suite() { :; }'
    for rel in "$@"; do
      case "$rel" in
        scripts/*) printf 'run_suite "%s" "$HERE/%s"\n' "${rel##*/}" "${rel#scripts/}" ;;
        skills/*)  printf 'run_suite "%s" "$SKILLS/%s"\n' "${rel##*/}" "${rel#skills/}" ;;
        *) printf 'run_suite "%s" "$ROOT/%s"\n' "${rel##*/}" "$rel" ;;
      esac
    done
  } > "$out"
}

# run_sut <dir> <ci-only-file>  — stdout captured, rc in $rc
run_sut() {
  local dir="$1" ci="$2"
  out="$(bash "$SUT" --plugin-root "$dir" --validate "$dir/scripts/validate.sh" \
         --ci-only-file "$ci" 2>/dev/null)"
  rc=$?
}

empty_list="$work/empty.txt"
: > "$empty_list"

# --- agreement is silent and exits 0 ---------------------------------------------

agree="$work/agree"
make_tree "$agree" scripts/tests/a.test.sh skills/x/scripts/tests/b.test.sh
make_validate "$agree" scripts/tests/a.test.sh skills/x/scripts/tests/b.test.sh
run_sut "$agree" "$empty_list"
if [ -z "$out" ] && [ "$rc" -eq 0 ]; then
  ok "agreement: silent on stdout and exits 0"
else
  bad "agreement: rc=$rc, stdout: $out"
fi

# --- a suite on disk that nothing registers and nothing excuses --------------------
# This is the #781 defect itself: CI's find sweep runs it, validate.sh does not,
# and reading validate.sh alone says it does not exist.

drift="$work/drift"
make_tree "$drift" scripts/tests/a.test.sh scripts/tests/orphan.test.sh
make_validate "$drift" scripts/tests/a.test.sh
run_sut "$drift" "$empty_list"
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q 'scripts/tests/orphan.test.sh runs in CI but not in validate.sh'; then
  ok "unregistered suite: named, and the run fails"
else
  bad "unregistered suite: rc=$rc, stdout: $out"
fi
if printf '%s' "$out" | grep -q 'scripts/tests/a.test.sh'; then
  bad "unregistered suite: reported a suite that IS registered: $out"
else
  ok "unregistered suite: a registered sibling is not dragged into the report"
fi

# --- a named opt-out suppresses exactly one suite, and only with a reason ----------

ci="$work/ci-only.txt"
printf 'scripts/tests/orphan.test.sh%sneeds a thing only CI provisions\n' "$TAB" > "$ci"
run_sut "$drift" "$ci"
if [ -z "$out" ] && [ "$rc" -eq 0 ]; then
  ok "named opt-out: the excused suite stops being a finding"
else
  bad "named opt-out: rc=$rc, stdout: $out"
fi

noreason="$work/no-reason.txt"
printf 'scripts/tests/orphan.test.sh\n' > "$noreason"
run_sut "$drift" "$noreason"
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q 'no reason'; then
  ok "opt-out without a reason: rejected — an unexplained exemption is the same silence"
else
  bad "opt-out without a reason: rc=$rc, stdout: $out"
fi

# --- the opt-out list cannot go stale ---------------------------------------------
# Both directions. An entry whose suite was deleted, and an entry whose suite was
# since registered — in which case the stated reason is a lie a reader believes.

stale="$work/stale.txt"
printf 'scripts/tests/orphan.test.sh%sneeds a thing only CI provisions\nscripts/tests/gone.test.sh%swas deleted three PRs ago\n' "$TAB" "$TAB" > "$stale"
run_sut "$drift" "$stale"
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q 'scripts/tests/gone.test.sh is on the CI-only list but no longer exists'; then
  ok "stale opt-out: an entry outliving its suite fails"
else
  bad "stale opt-out: rc=$rc, stdout: $out"
fi

both="$work/both.txt"
printf 'scripts/tests/a.test.sh%sclaims to be CI-only while validate.sh runs it\n' "$TAB" > "$both"
run_sut "$drift" "$both"
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q 'scripts/tests/a.test.sh is on the CI-only list AND registered'; then
  ok "contradiction: excused and registered at once fails"
else
  bad "contradiction: rc=$rc, stdout: $out"
fi

# --- a registration pointing at nothing -------------------------------------------
# run_suite reports this as "missing", which reads like a typo in a path rather
# than a registration no file backs.

ghost="$work/ghost"
make_tree "$ghost" scripts/tests/a.test.sh
make_validate "$ghost" scripts/tests/a.test.sh scripts/tests/never-existed.test.sh
run_sut "$ghost" "$empty_list"
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q 'registers scripts/tests/never-existed.test.sh, which is not on disk'; then
  ok "phantom registration: named, and the run fails"
else
  bad "phantom registration: rc=$rc, stdout: $out"
fi

# --- a registration the resolver cannot read is reported, never dropped ------------
# Silently skipping an unparseable line would let a suite vanish from the
# comparison while the gate still printed PASS — the exact shape of the bug.

odd="$work/odd"
make_tree "$odd" scripts/tests/a.test.sh
make_validate "$odd" scripts/tests/a.test.sh
printf 'run_suite "mystery.test.sh" "$SOMEWHERE/tests/mystery.test.sh"\n' >> "$odd/scripts/validate.sh"
run_sut "$odd" "$empty_list"
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q 'unparseable registration'; then
  ok "unparseable registration: reported rather than silently dropped"
else
  bad "unparseable registration: rc=$rc, stdout: $out"
fi

# --- degenerate inputs are failures, not clean runs --------------------------------
# A find that matches nothing and a validate.sh that registers nothing both
# compare "equal" under naive set arithmetic. Both mean the gate lost its
# subject, and reporting agreement would be the false pass this gate removes.

bare="$work/bare"
make_tree "$bare"
make_validate "$bare"
run_sut "$bare" "$empty_list"
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q 'no \*.test.sh found'; then
  ok "empty tree: reported as a broken enumeration, not as agreement"
else
  bad "empty tree: rc=$rc, stdout: $out"
fi

noreg="$work/noreg"
make_tree "$noreg" scripts/tests/a.test.sh
make_validate "$noreg"
run_sut "$noreg" "$empty_list"
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q 'registers no suite at all'; then
  ok "no run_suite block: reported as the block being gone, not as agreement"
else
  bad "no run_suite block: rc=$rc, stdout: $out"
fi

if bash "$SUT" --validate "$work/does-not-exist.sh" >/dev/null 2>&1; then
  bad "missing validate.sh: exited 0 instead of refusing"
else
  rc=$?
  [ "$rc" -eq 2 ] && ok "missing validate.sh: refuses with exit 2, never a silent pass" \
                  || bad "missing validate.sh: rc=$rc, expected 2"
fi

# --- --list accounts for every suite on disk --------------------------------------

out="$(bash "$SUT" --plugin-root "$drift" --validate "$drift/scripts/validate.sh" \
       --ci-only-file "$ci" --list 2>/dev/null)"
if printf '%s' "$out" | grep -q '^registered   scripts/tests/a.test.sh$' \
   && printf '%s' "$out" | grep -q '^ci-only      scripts/tests/orphan.test.sh — needs a thing only CI provisions$'; then
  ok "--list: each suite carries its disposition, and a CI-only one carries its reason"
else
  bad "--list: $out"
fi

# --- the real tree, with the real opt-out list ------------------------------------
# The case that matters. Everything above proves the mechanism; this proves the
# repository currently satisfies it, which is what gate 21 asserts on every run.

out="$(bash "$SUT" 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && [ -z "$out" ]; then
  ok "real tree: disk and validate.sh's run_suite block agree"
else
  bad "real tree: rc=$rc, output: $out"
fi

# This suite's own registration, asserted directly rather than left to the case
# above: if it were ever dropped from the block, the real-tree case would still
# pass the moment someone "fixed" it by adding a CI-only entry instead.
if grep -q 'run_suite "check-suite-registry.test.sh"' "$PLUGIN/scripts/validate.sh"; then
  ok "recursion: the reconciler's own suite is registered in the block it reconciles"
else
  bad "recursion: check-suite-registry.test.sh is not registered in validate.sh"
fi

echo
echo "check-suite-registry.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
