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
# suites is the per-module tally; "mod_a:2/2/0" reads collected/executed/failed.
suites() { node -e 'console.log(JSON.parse(process.argv[1]).suites.map(s=>`${s.module}:${s.collected}/${s.executed}/${s.failed}`).join(" "))' "$1"; }
# The stub records one argv entry per line, so a fixed-string line match is an
# exact assertion about what Odoo was actually invoked with.
argv_has() { grep -qxF -- "$1" "$work/args" && echo yes || echo no; }
argv_grep() { grep -qF -- "$1" "$work/args" && echo yes || echo no; }

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

# The core addons path is resolved by asking the RUNNING Odoo's interpreter where
# odoo.__file__ is, so the stub stands in for that interpreter. There is no real
# Odoo here to import, which is the point: the answer must come from the process
# that would run the tests, never from a config file.
#
# It answers ONLY that question and execs the real python3 for everything else.
# A stub that exited 0 for every invocation also answered browser-ensure.sh's
# "import websocket" probe, which silently made the no-browser refusal below
# unreachable on any host that ships Chrome: green in a devcontainer, red on a
# CI runner. A stub must never widen its own remit.
mkdir -p "$work/core-addons"
real_python3="$(command -v python3 || true)"
cat > "$bin/python3" <<STUB
#!/usr/bin/env bash
case "\${2-}" in
  *"import odoo"*) printf '%s\n' "$work/core-addons"; exit 0 ;;
esac
exec "${real_python3:-/usr/bin/python3}" "\$@"
STUB
chmod +x "$bin"/*

# The conf's addons_path is POISON: it names a tree this run must never inherit.
# Every assertion below that greps the recorded argv for it is asserting the fix
# for #887 — a broken sibling directory in the ambient config used to abort the
# registry and report the run as zero tests and zero failures.
conf="$work/odoo.conf"
printf '[options]\naddons_path = %s/poison-from-conf\n' "$work" > "$conf"
mkdir -p "$work/addons" "$work/poison-from-conf"

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
expect "green status"        "$(field "$out" status)"     "passed"
expect "green tallies its one suite" "$(suites "$out")"   "mymodule:2/2/0"
expect "green drops its db"  "$(field "$out" db_dropped)" "true"
expect "db name carries project+task" "$(field "$out" db)" "myrepo_test_4242"

out="$(run "$twophase" 0)"
expect "both phases summed" "$(field "$out" tests_run)" "10"

# ---------- addons path and data dir are STATED, never inherited (#887) --------
out="$(run "$green" 0)"
expect "addons path is worktree + core, in that order" \
  "$(argv_has "--addons-path=$work/addons/.worktrees/task-4242,$work/core-addons")" "yes"
expect "odoo.conf's addons_path never reaches the argv" \
  "$(argv_grep "$work/poison-from-conf")" "no"
expect "addons path is reported" "$(field "$out" addons_path)" \
  "$work/addons/.worktrees/task-4242,$work/core-addons"

# Odoo appends <data_dir>/addons/<series> to the addons path whatever
# --addons-path says, so an unstated data dir lets the ambient tree back in.
expect "data dir is stated, under this run's artifacts" \
  "$(argv_has "--data-dir=$work/artifacts/data/myrepo_test_4242")" "yes"
expect "data dir is reported" "$(field "$out" data_dir)" "$work/artifacts/data/myrepo_test_4242"
[ -d "$work/artifacts/data/myrepo_test_4242" ]
expect "data dir is created" "$?" "0"

mkdir -p "$work/override-addons"
out="$(run "$green" 0 --addons-path "$work/override-addons")"
expect "--addons-path override is honoured verbatim" \
  "$(argv_has "--addons-path=$work/override-addons")" "yes"
expect "override does not smuggle the conf path back in" \
  "$(argv_grep "$work/poison-from-conf")" "no"
expect "override run still passes" "$(field "$out" passed)" "true"

out="$(run "$green" 0 --data-dir "$work/own-data")"
expect "--data-dir override is honoured" "$(argv_has "--data-dir=$work/own-data")" "yes"

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
expect "red's status is failed, not registry_aborted" "$(field "$out" status)" "failed"
expect "a failing test is not an aborted registry" "$(field "$out" error)" "null"

# ---------- registry aborted is not "this module has no tests" (#887) ----------
# The registry dies before collection starts: no test is collected, so tests_run
# is 0 and no FAIL:/ERROR: line exists to extract. That used to render exactly
# like a module that ships no tests.
aborted="$work/aborted.log"
cat > "$aborted" <<'LOG'
2026-09-04 16:48:01,000 1 INFO db odoo.modules.loading: loading 1 modules...
2026-09-04 16:48:02,000 1 ERROR db odoo.modules.loading: Failed to load registry
Traceback (most recent call last):
  File "/usr/lib/python3/dist-packages/odoo/modules/registry.py", line 88, in new
    odoo.modules.load_modules(registry, force_demo, status, update_module)
ImportError: cannot import name '_ignore_tax_lock_date' from 'odoo.addons.account.models.account_move_line'
2026-09-04 16:48:02,100 1 CRITICAL db odoo.service.server: Failed to initialize database
LOG
out="$(run "$aborted" 1)"
expect "aborted registry gets its own status" "$(field "$out" status)" "registry_aborted"
expect "aborted registry is never a pass"     "$(field "$out" passed)" "false"
expect "aborted registry collected no tests"  "$(field "$out" tests_run)" "0"
expect "aborted registry quotes the decisive error" "$(field "$out" error)" \
  "ImportError: cannot import name '_ignore_tax_lock_date' from 'odoo.addons.account.models.account_move_line'"
expect "aborted registry records a failure rather than an empty list" "$(nfail "$out")" "1"

# The looser rule: zero tests plus a traceback is a run that died, whatever
# logger reported it.
crashload="$work/crashload.log"
cat > "$crashload" <<'LOG'
2026-09-04 16:48:01,000 1 INFO db odoo.service.server: Odoo version 18.0
Traceback (most recent call last):
  File "/usr/lib/python3/dist-packages/odoo/cli/server.py", line 180, in main
    rc = odoo.service.server.start(preload=preload, stop=stop)
psycopg2.OperationalError: could not connect to server
LOG
out="$(run "$crashload" 1)"
expect "zero tests + traceback is an abort" "$(field "$out" status)" "registry_aborted"
expect "abort quotes the exception line"    "$(field "$out" error)" \
  "psycopg2.OperationalError: could not connect to server"

# ---------- per-suite collected vs executed ------------------------------------
# Odoo logs no per-module collected count distinct from the executed one, so the
# two carry the same number and collected_is_executed says so. What the tally
# does buy is WHICH module ran nothing at all.
twosuites="$work/twosuites.log"
cat > "$twosuites" <<'LOG'
2026-09-04 16:48:21,163 1 INFO db odoo.addons.mod_a.tests.test_a: Starting TestA.test_one ...
2026-09-04 16:48:21,663 1 INFO db odoo.addons.mod_a.tests.test_a: Starting TestA.test_two ...
2026-09-04 16:48:22,163 1 INFO db odoo.addons.mod_b.tests.test_b: Starting TestB.test_one ...
2026-09-04 16:48:22,900 1 ERROR db odoo.addons.mod_b.tests.test_b: FAIL: TestB.test_one
Traceback (most recent call last):
  File "/x/test_b.py", line 9, in test_one
    self.assertTrue(False)
AssertionError: False is not true
2026-09-04 16:48:29,622 1 INFO db odoo.tests.result: 1 failed, 0 error(s) of 3 tests when loading database 'db'
LOG
out="$(run "$twosuites" 1)"
expect "per-suite counts, one line per module" "$(suites "$out")" "mod_a:2/2/0 mod_b:1/1/1"
expect "two suites still sum to the run total"  "$(field "$out" tests_run)" "3"
expect "collected is flagged as the executed count" "$(field "$out" collected_is_executed)" "true"
expect "a failing suite fails the run" "$(field "$out" status)" "failed"

# ---------- fail-closed ---------------------------------------------------------
# An empty log is the shape a crashed or unrecognized run leaves behind. Zero
# executed tests must never read as green, whatever the exit code says.
out="$(run "$work/empty.log" 0)"
expect "empty log runs no tests" "$(field "$out" tests_run)" "0"
expect "zero tests is not a pass" "$(field "$out" passed)" "false"
# No traceback and no loading error: this is a silent zero, not an abort. The two
# are reported differently on purpose — only one of them names a cause.
expect "a silent zero is failed, not registry_aborted" "$(field "$out" status)" "failed"
expect "silent zero has no suites to tally" "$(suites "$out")" ""

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
#
# "No browser available" is MANUFACTURED here rather than assumed: every
# candidate Odoo looks for is shadowed on PATH by an executable that fails
# --version, which is precisely what browser-ensure.sh's usable() rejects and
# what Odoo itself treats as no browser at all. Relying on the host not to have
# Chrome made this case pass in a devcontainer and fail on a CI runner, where
# google-chrome is installed — a test that passes because of what the machine
# lacks is testing the machine.
nobrowser="$work/nobrowser"; mkdir -p "$nobrowser"
for candidate in google-chrome chromium chromium-browser google-chrome-stable; do
  printf '#!/usr/bin/env bash\nexit 1\n' > "$nobrowser/$candidate"
done
chmod +x "$nobrowser"/*

# The refusal has to come from the browser search itself, not from some later
# check that happens to be unsatisfiable here — so assert on the reason too.
berr="$work/browser-ensure.err"
rc=0
PATH="$nobrowser:$bin:$PATH" ODOO_BROWSER_BIN="" \
  PLAYWRIGHT_BROWSERS_PATH="$work/no-browsers" \
  bash "$(dirname "$SUT")/browser-ensure.sh" >/dev/null 2>"$berr" || rc=$?
expect "browser-ensure refuses when every candidate is unusable" "$rc" "6"
case "$(cat "$berr")" in *"no usable headless browser found"*) pass=$((pass+1)) ;;
  *) fail=$((fail+1)); echo "FAIL refusal should name the browser search: $(cat "$berr")" >&2 ;; esac

rc=0
PATH="$nobrowser:$bin:$PATH" ODOO_RC="$conf" ODOO_FAKE_LOG="$green" ODOO_FAKE_RC=0 \
  ODOO_FAKE_ARGS="$work/args" ODOO_TEST_ARTIFACTS_DIR="$work/artifacts" \
  WORKTREE_ADDONS_BASE="$work/addons" \
  ODOO_BROWSER_BIN="" PLAYWRIGHT_BROWSERS_PATH="$work/no-browsers" \
  bash "$SUT" myrepo 4242 mymodule --with-tours >/dev/null 2>&1 || rc=$?
expect "no browser + --with-tours exits 6" "$rc" "6"

echo "{\"passed\": $pass, \"failed\": $fail}"
[ "$fail" -eq 0 ]
