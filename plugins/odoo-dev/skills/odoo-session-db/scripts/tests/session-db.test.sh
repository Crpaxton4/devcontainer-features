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

# ---------- run, against the REAL run-tests.sh ---------------------------------
bin="$work/bin"; mkdir -p "$bin" "$work/addons" "$work/core-addons"
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

echo "{\"passed\": $pass, \"failed\": $fail}"
[ "$fail" -eq 0 ]
