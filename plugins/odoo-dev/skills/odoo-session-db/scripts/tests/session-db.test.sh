#!/usr/bin/env bash
# session-db.test.sh — offline tests for session-db.sh.
#
# Two layers. Most cases stub run-tests.sh itself, so they test only what this
# script adds. The last cases run the REAL run-tests.sh against a stub odoo and
# stub createdb/dropdb that record their argv, so the claim "the database this
# session creates is the one it drops" is checked against the real naming code
# and not against a copy of it.
#
# No network, no docker, no postgres, no Odoo.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$HERE/../session-db.sh"
REAL_RUN_TESTS="$HERE/../../../odoo-test-run/scripts/run-tests.sh"
work="$(mktemp -d "${TMPDIR:-/tmp}/session-db-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
expect() { if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted '$3', got '$2'" >&2; fi; }

# ---------- suffix and name ----------------------------------------------------
a="$(bash "$SUT" suffix --session-id sess-A)"
b="$(bash "$SUT" suffix --session-id sess-B)"
expect "suffix has the shape _s<8 hex>"    "$(printf '%s' "$a" | grep -cE '^_s[0-9a-f]{8}$')" "1"
expect "same session id, same suffix"      "$(bash "$SUT" suffix --session-id sess-A)" "$a"
expect "different session ids differ"      "$([ "$a" != "$b" ] && echo yes || echo no)" "yes"
r1="$(bash "$SUT" suffix)"; r2="$(bash "$SUT" suffix)"
expect "no session id gives a random suffix" "$([ "$r1" != "$r2" ] && echo yes || echo no)" "yes"
expect "SESSION_DB_ID is read"             "$(SESSION_DB_ID=sess-A bash "$SUT" suffix)" "$a"
expect "--session-id wins over SESSION_DB_ID" "$(SESSION_DB_ID=sess-B bash "$SUT" suffix --session-id sess-A)" "$a"

n1="$(bash "$SUT" name QOC 4242 --session-id sess-A)"
n2="$(bash "$SUT" name QOC 4242 --session-id sess-B)"
expect "name is <project>_test_<id><suffix>" "$n1" "qoc_test_4242$a"
expect "two sessions, one task, two names"   "$([ "$n1" != "$n2" ] && echo yes || echo no)" "yes"
expect "repo is lowercased and stripped"     "$(bash "$SUT" name 'My.Repo!' 7 --session-id sess-A)" "myrepo_test_7$a"

rc=0; bash "$SUT" name '!!!' 7 >/dev/null 2>&1 || rc=$?
expect "a repo that normalizes to nothing exits 2" "$rc" "2"
rc=0; bash "$SUT" name "$(printf 'x%.0s' $(seq 1 60))" 7 >/dev/null 2>&1 || rc=$?
expect "a name over 63 bytes exits 2" "$rc" "2"
rc=0; bash "$SUT" bogus >/dev/null 2>&1 || rc=$?
expect "unknown command exits 2" "$rc" "2"
rc=0; bash "$SUT" run r 1 m --db-suffix _x >/dev/null 2>&1 || rc=$?
expect "run refuses a caller --db-suffix" "$rc" "2"

# ---------- run, with run-tests.sh stubbed -------------------------------------
stub="$work/stub-run-tests.sh"
cat > "$stub" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$STUB_ARGS"
printf 'noise line\n%s\n' "$STUB_JSON"
[ -z "${STUB_TRAILER:-}" ] || printf '%s\n' "$STUB_TRAILER"
exit "${STUB_RC:-0}"
STUB
chmod +x "$stub"
want="qoc_test_4242$a"

export STUB_ARGS="$work/stub-args"
export STUB_JSON
STUB_JSON="{\"db\":\"$want\",\"db_dropped\":true,\"passed\":true}"
out="$(SESSION_DB_RUN_TESTS="$stub" bash "$SUT" run QOC 4242 mod --session-id sess-A --with-tours 2>/dev/null)"
expect "run prints the result JSON last" "$(printf '%s\n' "$out" | tail -1)" "$STUB_JSON"
expect "run passes --db-suffix"          "$(grep -cxF -- "$a" "$STUB_ARGS")" "1"
expect "run forwards other options"      "$(grep -cxF -- --with-tours "$STUB_ARGS")" "1"
expect "run passes repo, id, module first" "$(head -3 "$STUB_ARGS" | tr '\n' ' ')" "QOC 4242 mod "

STUB_JSON='{"db":"qoc_test_4242","db_dropped":true}'
rc=0; SESSION_DB_RUN_TESTS="$stub" bash "$SUT" run QOC 4242 mod --session-id sess-A >/dev/null 2>&1 || rc=$?
expect "a database name that disagrees exits 3" "$rc" "3"

STUB_JSON="{\"db\":\"$want\",\"db_dropped\":false}"
rc=0; SESSION_DB_RUN_TESTS="$stub" bash "$SUT" run QOC 4242 mod --session-id sess-A >/dev/null 2>&1 || rc=$?
expect "a database that was not dropped exits 3" "$rc" "3"

STUB_JSON='not json'
rc=0; SESSION_DB_RUN_TESTS="$stub" bash "$SUT" run QOC 4242 mod --session-id sess-A >/dev/null 2>&1 || rc=$?
expect "a result that is not JSON exits 3" "$rc" "3"

# Item 1: a failing run keeps its JSON and its exit code.
STUB_JSON="{\"db\":\"$want\",\"db_dropped\":true,\"status\":\"failed\",\"passed\":false}"
rc=0; out="$(STUB_RC=1 SESSION_DB_RUN_TESTS="$stub" bash "$SUT" run QOC 4242 mod --session-id sess-A 2>/dev/null)" || rc=$?
expect "a failing run exits with the run-tests.sh code" "$rc" "1"
expect "a failing run still prints its JSON" "$(printf '%s\n' "$out" | tail -1)" "$STUB_JSON"
rc=0; STUB_RC=5 SESSION_DB_RUN_TESTS="$stub" bash "$SUT" run QOC 4242 mod --session-id sess-A >/dev/null 2>&1 || rc=$?
expect "another exit code is passed through" "$rc" "5"

# Item 2: a trailing line after the JSON does not hide the result.
STUB_JSON="{\"db\":\"$want\",\"db_dropped\":true,\"status\":\"passed\"}"
rc=0; out="$(STUB_TRAILER='warn: x' SESSION_DB_RUN_TESTS="$stub" bash "$SUT" run QOC 4242 mod --session-id sess-A 2>/dev/null)" || rc=$?
expect "a trailing warning line gives exit 0" "$rc" "0"
expect "the JSON is printed with the warning" "$(printf '%s\n' "$out" | grep -c '"status":"passed"')" "1"

# Item 3: series_mismatch created no database, so db_dropped false is legitimate.
STUB_JSON="{\"db\":\"$want\",\"db_dropped\":false,\"status\":\"series_mismatch\",\"passed\":false}"
rc=0; SESSION_DB_RUN_TESTS="$stub" bash "$SUT" run QOC 4242 mod --session-id sess-A >/dev/null 2>&1 || rc=$?
expect "series_mismatch is not exit 3" "$rc" "0"
STUB_JSON="{\"db\":\"$want\",\"db_dropped\":false,\"status\":\"failed\"}"
rc=0; SESSION_DB_RUN_TESTS="$stub" bash "$SUT" run QOC 4242 mod --session-id sess-A >/dev/null 2>&1 || rc=$?
expect "db_dropped false on any other status is still exit 3" "$rc" "3"

# Item 4: bytes, not characters, in any locale; and a task id charset.
e25="$(printf 'é%.0s' $(seq 1 25))"
for loc in C.UTF-8 C; do
  rc=0; LC_ALL=$loc bash "$SUT" name qoc "$e25" --session-id s >/dev/null 2>&1 || rc=$?
  expect "multibyte task id over 63 bytes exits 2 under $loc" "$rc" "2"
done
rc=0; bash "$SUT" name qoc "a b;c" --session-id s >/dev/null 2>&1 || rc=$?
expect "a task id with a space and a semicolon exits 2" "$rc" "2"
rc=0; bash "$SUT" name qoc "" --session-id s >/dev/null 2>&1 || rc=$?
expect "an empty task id exits 2" "$rc" "2"
rc=0; bash "$SUT" name qoc AB_c-9 --session-id s >/dev/null 2>&1 || rc=$?
expect "a task id of letters, digits, underscore, hyphen is accepted" "$rc" "0"

# Item 5: --session-id errors, and the --db-suffix forms.
rc=0; bash "$SUT" name qoc 1 --session-id "" >/dev/null 2>&1 || rc=$?
expect "an empty --session-id exits 2" "$rc" "2"
rc=0; bash "$SUT" name qoc 1 --session-id >"$work/sid.out" 2>"$work/sid.err" || rc=$?
expect "--session-id with no value exits 2" "$rc" "2"
expect "--session-id with no value says what is wrong" "$(grep -c 'non-empty value' "$work/sid.err")" "1"
rc=0; bash "$SUT" run qoc 1 m --db-suffix=x >/dev/null 2>&1 || rc=$?
expect "--db-suffix=x is refused" "$rc" "2"
rc=0; bash "$SUT" run qoc 1 m -- --db-suffix x >/dev/null 2>&1 || rc=$?
expect "--db-suffix after -- is refused" "$rc" "2"

# ---------- run, against the REAL run-tests.sh ---------------------------------
bin="$work/bin"; mkdir -p "$bin" "$work/addons" "$work/core-addons"
# run-tests.sh refuses (exit 2) when no custom addons root holds the module (#1171).
mkdir -p "$work/addons/.worktrees/task-4242"
cat > "$bin/odoo" <<'STUB'
#!/usr/bin/env bash
case "${1:-}" in --version) echo "Odoo Server 18.0-20260810"; exit 0 ;; esac
echo "2026-09-04 16:48:29,622 1 INFO db odoo.tests.result: 0 failed, 0 error(s) of 2 tests when loading database 'db'"
exit 0
STUB
printf '#!/usr/bin/env bash\nexit 0\n' > "$bin/pg_isready"
for tool in createdb dropdb; do
  cat > "$bin/$tool" <<STUB
#!/usr/bin/env bash
echo "$tool \$*" >> "\$DB_CALLS"
exit 0
STUB
done
real_python3="$(command -v python3 || true)"
cat > "$bin/python3" <<STUB
#!/usr/bin/env bash
case "\${2-}" in
  *"import odoo"*) printf '%s\n' "$work/core-addons"; exit 0 ;;
esac
exec "${real_python3:-/usr/bin/python3}" "\$@"
STUB
chmod +x "$bin"/*
printf '[options]\n' > "$work/odoo.conf"

real_run() { # real_run <session-id>
  PATH="$bin:$PATH" ODOO_RC="$work/odoo.conf" DB_CALLS="$work/db-calls" \
    ODOO_TEST_ARTIFACTS_DIR="$work/artifacts" WORKTREE_ADDONS_BASE="$work/addons" \
    SESSION_DB_RUN_TESTS="$REAL_RUN_TESTS" \
    bash "$SUT" run QOC 4242 mymodule --session-id "$1" 2>/dev/null | tail -1
}
dbfield() { node -e 'console.log(String(JSON.parse(process.argv[1]).db))' "$1"; }

: > "$work/db-calls"
resA="$(real_run sess-A)"
nameA="$(bash "$SUT" name QOC 4242 --session-id sess-A)"
expect "real run-tests.sh reports the name this script computed" "$(dbfield "$resA")" "$nameA"
expect "the database created is the database dropped last" \
  "$(grep '^createdb' "$work/db-calls" | awk '{print $NF}')" "$nameA"
expect "the final dropdb names the same database" \
  "$(grep '^dropdb' "$work/db-calls" | tail -1 | awk '{print $NF}')" "$nameA"

: > "$work/db-calls"
real_run sess-B >/dev/null
nameB="$(bash "$SUT" name QOC 4242 --session-id sess-B)"
expect "a second session never touches the first session's database" \
  "$(grep -cF "$nameA" "$work/db-calls")" "0"
expect "the second session uses its own name" \
  "$(grep '^createdb' "$work/db-calls" | awk '{print $NF}')" "$nameB"

# Two live runs on one session id use one name: the id is the session, so the
# promise is "same id, same name", and two ids never share one (checked above).
: > "$work/db-calls"
real_run sess-A >/dev/null; real_run sess-A >/dev/null
expect "two runs on one id use one database name" \
  "$(grep '^createdb' "$work/db-calls" | awk '{print $NF}' | sort -u | wc -l | tr -d ' ')" "1"

echo "{\"passed\": $pass, \"failed\": $fail}"
[ "$fail" -eq 0 ]
