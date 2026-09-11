#!/usr/bin/env bash
# run-tests.test.sh — offline tests for run-tests.sh's evidence extraction.
#
# The whole value of run-tests.sh is that its numbers can be trusted by a PR
# gate, so the numbers are what gets tested. A stub `odoo` on PATH emits a canned
# Odoo log and a canned exit code; everything downstream — mode detection, the
# summary parsing, the failure extraction, the fail-closed rules — is real.
#
# This exists because the first live run died on an empty `grep`: under
# `set -o pipefail` a no-match exits 1 and aborts the script at precisely the
# moment the correct answer is zero. That is invisible in a happy-path run.
#
# No network, no docker, no postgres, no Odoo.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$(cd "$SCRIPT_DIR/.." && pwd)/run-tests.sh"
work="$(mktemp -d "${TMPDIR:-/tmp}/run-tests-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
expect() { if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted '$3', got '$2'" >&2; fi; }
field() { node -e 'console.log(String(JSON.parse(process.argv[1])[process.argv[2]] ?? "null"))' "$1" "$2"; }
nfail() { node -e 'console.log(JSON.parse(process.argv[1]).failures.length)' "$1"; }
firsterr() { node -e 'const f=JSON.parse(process.argv[1]).failures[0];console.log(f?f.error:"")' "$1"; }

# ---------- stubbed environment ------------------------------------------------
bin="$work/bin"; mkdir -p "$bin"
cat > "$bin/odoo" <<'STUB'
#!/usr/bin/env bash
case "${1:-}" in
  --version) echo "Odoo Server 18.0-20260810"; exit 0 ;;
esac
printf '%s\n' "$@" > "$ODOO_FAKE_ARGS"
cat "$ODOO_FAKE_LOG"
exit "${ODOO_FAKE_RC:-0}"
STUB
for tool in pg_isready createdb dropdb; do
  printf '#!/usr/bin/env bash\nexit 0\n' > "$bin/$tool"
done
chmod +x "$bin"/*

conf="$work/odoo.conf"
printf '[options]\naddons_path = %s/addons\n' "$work" > "$conf"
mkdir -p "$work/addons"

run() { # run <log-fixture> <rc> [extra args...]
  local log="$1" rc="$2"; shift 2
  PATH="$bin:$PATH" ODOO_RC="$conf" ODOO_FAKE_LOG="$log" ODOO_FAKE_RC="$rc" \
    ODOO_FAKE_ARGS="$work/args" ODOO_TEST_ARTIFACTS_DIR="$work/artifacts" \
    WORKTREE_ADDONS_BASE="$work/addons" \
    bash "$SUT" myrepo 4242 mymodule "$@" 2>/dev/null | tail -1
}

# ---------- fixtures -----------------------------------------------------------
green="$work/green.log"
cat > "$green" <<'LOG'
2026-09-04 16:48:21,163 1 INFO db odoo.addons.mymodule.tests.test_a: Starting TestA.test_one ...
2026-09-04 16:48:22,163 1 INFO db odoo.addons.mymodule.tests.test_a: Starting TestA.test_two ...
2026-09-04 16:48:29,622 1 INFO db odoo.tests.stats: mymodule: 2 tests 8.48s 293 queries
2026-09-04 16:48:29,622 1 INFO db odoo.tests.result: 0 failed, 0 error(s) of 2 tests when loading database 'db'
LOG

# at_install and post_install each emit a summary; taking only the last one
# undercounted every run that had both.
twophase="$work/twophase.log"
cat > "$twophase" <<'LOG'
2026-09-04 16:48:29,000 1 INFO db odoo.tests.result: 0 failed, 0 error(s) of 7 tests when loading database 'db'
2026-09-04 16:48:39,000 1 INFO db odoo.service.server: 3 post-tests in 28.95s, 673 queries
2026-09-04 16:48:39,622 1 INFO db odoo.tests.result: 0 failed, 0 error(s) of 3 tests when loading database 'db'
LOG

red="$work/red.log"
cat > "$red" <<'LOG'
2026-09-04 16:48:21,163 1 INFO db odoo.addons.mymodule.tests.test_a: Starting TestA.test_one ...
2026-09-04 16:48:22,000 1 ERROR db odoo.addons.mymodule.tests.test_a: FAIL: TestA.test_one
Traceback (most recent call last):
  File "/x/test_a.py", line 9, in test_one
    self.assertEqual(2, 1)
AssertionError: 2 != 1
2026-09-04 16:48:29,622 1 INFO db odoo.tests.result: 1 failed, 0 error(s) of 1 tests when loading database 'db'
LOG

toured="$work/toured.log"
cat > "$toured" <<'LOG'
2026-09-04 16:48:21,163 1 INFO db odoo.addons.mymodule.tests.test_tour: Starting TestTour.test_tour ...
2026-09-04 16:48:22,018 1 INFO db odoo...browser: [1/9] Tour my_tour → Step check (trigger: .x)
2026-09-04 16:48:23,018 1 INFO db odoo...browser: [2/9] Tour my_tour → Step click (trigger: .y)
2026-09-04 16:48:29,529 1 INFO db odoo...browser: tour succeeded
2026-09-04 16:48:29,622 1 INFO db odoo.tests.result: 0 failed, 0 error(s) of 1 tests when loading database 'db'
LOG

: > "$work/empty.log"

# ---------- extraction ----------------------------------------------------------
out="$(run "$green" 0)"
expect "green passes"        "$(field "$out" passed)"     "true"
expect "green counts tests"  "$(field "$out" tests_run)"  "2"
expect "mode is container"   "$(field "$out" mode)"       "container"
expect "green has no failures" "$(nfail "$out")"          "0"
expect "green drops its db"  "$(field "$out" db_dropped)" "true"
expect "db name carries project+task" "$(field "$out" db)" "myrepo_test_4242"

out="$(run "$twophase" 0)"
expect "both phases summed" "$(field "$out" tests_run)" "10"

# Screenshot redirection is unconditional, not tied to --with-tours: ANY failing
# HttpCase saves a screenshot, and the configured path need not be writable here.
# When it is not, "PermissionError: /mnt/recordings" replaces the real failure.
grep -q -- "--screenshots=$work/artifacts/screenshots" "$work/args"
expect "screenshots redirected without --with-tours" "$?" "0"
[ -d "$work/artifacts/screencasts" ]
expect "artifact dirs created" "$?" "0"

out="$(run "$red" 1)"
expect "red fails"            "$(field "$out" passed)" "false"
expect "red counts its test"  "$(field "$out" tests_run)" "1"
expect "red extracts one failure" "$(nfail "$out")" "1"
expect "error is the exception line, not the traceback" "$(firsterr "$out")" "AssertionError: 2 != 1"

# ---------- fail-closed ---------------------------------------------------------
# An empty log is the shape a crashed or unrecognized run leaves behind. Zero
# executed tests must never read as green, whatever the exit code says.
out="$(run "$work/empty.log" 0)"
expect "empty log runs no tests" "$(field "$out" tests_run)" "0"
expect "zero tests is not a pass" "$(field "$out" passed)" "false"

# ---------- tours ----------------------------------------------------------------
mkdir -p "$work/addons/.worktrees/task-4242/mymodule"
cat > "$work/addons/.worktrees/task-4242/mymodule/test_tour.py" <<'PY'
def test(self):
    self.start_tour("/odoo", "my_tour", login="admin")
PY

# Declared but not run: the silent-skip Odoo produces without a browser.
out="$(run "$green" 0)"
expect "tours declared from source" "$(field "$out" tours_declared)" "1"
expect "no tour ran"                "$(field "$out" tours_run)" "0"
expect "declared-but-unrun is NOT a pass" "$(field "$out" passed)" "false"
case "$(firsterr "$out")" in *"start_tour()"*) pass=$((pass+1)) ;;
  *) fail=$((fail+1)); echo "FAIL tour-gap failure should explain itself: $(firsterr "$out")" >&2 ;; esac

# Regression: a FAILING tour test prints its own source in the traceback, and
# that source line contains the literal success_signal="tour succeeded". A bare
# string match counted that as a passing tour on a run where none ever started.
crashed="$work/crashed.log"
cat > "$crashed" <<'LOG'
2026-09-04 17:03:00,000 1 INFO db odoo.addons.mymodule.tests.test_tour: Starting TestTour.test_tour ...
2026-09-04 17:03:01,000 1 ERROR db odoo.addons.mymodule.tests.test_tour: FAIL: TestTour.test_tour
Traceback (most recent call last):
  File "/usr/lib/python3/dist-packages/odoo/tests/common.py", line 501, in start_tour
    return self.browser_js(url_path=url_path, code=code, ready=ready, timeout=timeout, success_signal="tour succeeded", **kwargs)
PermissionError: [Errno 13] Permission denied: '/mnt/recordings'
2026-09-04 17:03:02,000 1 INFO db odoo.tests.result: 1 failed, 0 error(s) of 1 tests when loading database 'db'
LOG
out="$(run "$crashed" 1)"
expect "traceback source line is not a tour" "$(field "$out" tours_passed)" "0"
expect "crashed tour ran no tours"           "$(field "$out" tours_run)" "0"
expect "crashed tour is not a pass"          "$(field "$out" passed)" "false"

out="$(run "$toured" 0)"
expect "tour counted"        "$(field "$out" tours_run)" "1"
expect "tour success counted" "$(field "$out" tours_passed)" "1"
expect "toured run passes"   "$(field "$out" passed)" "true"

# --with-tours must refuse rather than run blind when no browser is available.
rc=0
PATH="$bin:$PATH" ODOO_RC="$conf" ODOO_FAKE_LOG="$green" ODOO_FAKE_RC=0 \
  ODOO_BROWSER_BIN="" PLAYWRIGHT_BROWSERS_PATH="$work/no-browsers" \
  bash "$SUT" myrepo 4242 mymodule --with-tours >/dev/null 2>&1 || rc=$?
expect "no browser + --with-tours exits 6" "$rc" "6"

echo "{\"passed\": $pass, \"failed\": $fail}"
[ "$fail" -eq 0 ]
