#!/usr/bin/env bash
# populate-db.test.sh — offline tests for populate-db.sh's plan and its verdict.
#
# The whole value of populate-db.sh is that it says no when `odoo populate` says
# nothing, so the no is what gets tested. A stub `odoo` on PATH answers `shell`
# with a canned resolved-order payload and `populate` with a canned Odoo log and
# exit code; a stub `psql` answers row counts from a table with a before and an
# after column. Everything in between — the exclusion closure, the log parsing,
# the traceback extraction, the fail-loud rule — is the real thing.
#
# The case this exists for: a log carrying "Something went wrong populating
# database" while the process exits 0. That is the shape of every swallowed
# failure, and treating it as success is the bug the script was written against.
#
# No network, no docker, no postgres, no Odoo.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$(cd "$SCRIPT_DIR/.." && pwd)/populate-db.sh"
work="$(mktemp -d "${TMPDIR:-/tmp}/populate-db-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
expect() { if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted '$3', got '$2'" >&2; fi; }
contains() { # contains <label> <haystack> <needle>
  case "$2" in *"$3"*) pass=$((pass+1)) ;;
    *) fail=$((fail+1)); echo "FAIL $1: '$3' not found in: $2" >&2 ;; esac
}
field() { node -e 'console.log(JSON.stringify(JSON.parse(process.argv[1])[process.argv[2]] ?? null))' "$1" "$2"; }
models() { node -e 'console.log(JSON.parse(process.argv[1]).models.map((m)=>m.model).join(","))' "$1"; }
modelfield() { node -e '
  const m = JSON.parse(process.argv[1]).models.find((x) => x.model === process.argv[2]);
  console.log(String(m ? m[process.argv[3]] : "missing"));
' "$1" "$2" "$3"; }

# ---------- stubbed environment ------------------------------------------------
bin="$work/bin"; mkdir -p "$bin"
cat > "$bin/odoo" <<'STUB'
#!/usr/bin/env bash
case "${1:-}" in
  shell) cat "$ODOO_FAKE_RESOLVED"; exit 0 ;;
  populate)
    printf '%s\n' "$@" > "$ODOO_FAKE_ARGS"
    : > "$PSQL_FAKE_PHASE"
    cat "$ODOO_FAKE_LOG"
    exit "${ODOO_FAKE_RC:-0}" ;;
esac
exit 2
STUB
cat > "$bin/psql" <<'STUB'
#!/usr/bin/env bash
q=""
while [ $# -gt 0 ]; do
  case "$1" in -c) q="${2:-}"; shift 2 ;; *) shift ;; esac
done
[ "$q" = "select 1" ] && { echo 1; exit 0; }
tbl="$(printf '%s' "$q" | sed -n 's/.*from "\([a-z0-9_]*\)".*/\1/p')"
[ -n "$tbl" ] || exit 1
col=2
[ -f "$PSQL_FAKE_PHASE" ] && col=3
awk -F'\t' -v t="$tbl" -v c="$col" '$1 == t { print $c; found = 1 } END { exit !found }' \
  "$PSQL_FAKE_COUNTS"
STUB
chmod +x "$bin/odoo" "$bin/psql"

# ---------- fixtures -----------------------------------------------------------
# Post-order, as _get_ordered_models emits it: res.currency is pulled in by
# res.partner's _populate_dependencies and was never asked for.
resolved="$work/resolved.json"
{
  printf '##ODOO-POPULATE-RESOLVED##'
  cat <<'JSON'
{"resolved":[
{"model":"res.currency","table":"res_currency","requested":false,"factories":true,"dependencies":[],"size_rows":10},
{"model":"res.partner","table":"res_partner","requested":true,"factories":true,"dependencies":["res.currency"],"size_rows":100},
{"model":"res.users","table":"res_users","requested":true,"factories":true,"dependencies":["res.partner"],"size_rows":100},
{"model":"account.move","table":"account_move","requested":true,"factories":true,"dependencies":["res.partner"],"size_rows":100},
{"model":"account.move.line","table":"account_move_line","requested":true,"factories":true,"dependencies":["account.move"],"size_rows":100}
],"unavailable":["sale.order"]}
JSON
} | tr -d '\n' > "$resolved"
printf '\n' >> "$resolved"

counts="$work/counts.tsv"
printf 'res_currency\t5\t15\nres_partner\t2\t102\nres_users\t1\t101\naccount_move\t0\t100\naccount_move_line\t0\t300\n' > "$counts"

green="$work/green.log"
cat > "$green" <<'LOG'
2026-09-20 10:00:00,000 1 INFO db odoo.cli.populate: Computing model order
2026-09-20 10:00:01,000 1 INFO db odoo.cli.populate: Populating database
2026-09-20 10:00:01,100 1 INFO db odoo.cli.populate: Populating database for model res.currency
2026-09-20 10:00:03,100 1 INFO db odoo.cli.populate: Populating database for model res.partner
2026-09-20 10:00:09,100 1 INFO db odoo.cli.populate: Populating database for model res.users
2026-09-20 10:00:12,100 1 INFO db odoo.cli.populate: Populating database for model account.move
2026-09-20 10:00:20,100 1 INFO db odoo.cli.populate: Populating database for model account.move.line
2026-09-20 10:00:31,100 1 INFO db odoo.modules.registry: Registry loaded
LOG

# The reported bug, verbatim in shape: a custom addon raises inside account.move's
# factory, the bare `except:` logs it, and the process still exits 0.
swallowed="$work/swallowed.log"
cat > "$swallowed" <<'LOG'
2026-09-20 10:00:01,100 1 INFO db odoo.cli.populate: Populating database for model res.currency
2026-09-20 10:00:03,100 1 INFO db odoo.cli.populate: Populating database for model res.partner
2026-09-20 10:00:09,100 1 INFO db odoo.cli.populate: Populating database for model res.users
2026-09-20 10:00:12,100 1 INFO db odoo.cli.populate: Populating database for model account.move
2026-09-20 10:00:14,100 1 ERROR db odoo.cli.populate: Something went wrong populating database
Traceback (most recent call last):
  File "/usr/lib/python3/dist-packages/odoo/cli/populate.py", line 60, in populate
    registry.populated_models[model._name] = model._populate(size).ids
  File "/mnt/extra-addons/custom_accounting/models/account_move.py", line 31, in action_post
    raise UserError("custom_reference is required")
odoo.exceptions.UserError: custom_reference is required
LOG

: > "$work/empty.log"

run() { # run <log-fixture> <rc> [args...] ; prints the last stdout line
  local log="$1" rc="$2"; shift 2
  rm -f "$work/phase"
  PATH="$bin:$PATH" TMPDIR="$work" \
    ODOO_FAKE_RESOLVED="$resolved" ODOO_FAKE_LOG="$log" ODOO_FAKE_RC="$rc" \
    ODOO_FAKE_ARGS="$work/args" PSQL_FAKE_COUNTS="$counts" PSQL_FAKE_PHASE="$work/phase" \
    bash "$SUT" --database testdb "$@" 2>"$work/stderr" | tail -1
}
rc_of() { # rc_of <log-fixture> <rc> [args...] ; prints the SUT's exit code
  local log="$1" rc="$2"; shift 2
  rm -f "$work/phase"
  PATH="$bin:$PATH" TMPDIR="$work" \
    ODOO_FAKE_RESOLVED="$resolved" ODOO_FAKE_LOG="$log" ODOO_FAKE_RC="$rc" \
    ODOO_FAKE_ARGS="$work/args" PSQL_FAKE_COUNTS="$counts" PSQL_FAKE_PHASE="$work/phase" \
    bash "$SUT" --database testdb "$@" >/dev/null 2>&1
  echo $?
}

# ---------- usage --------------------------------------------------------------
PATH="$bin:$PATH" bash "$SUT" >/dev/null 2>&1
expect "no --database is a usage error" "$?" "2"
PATH="$bin:$PATH" bash "$SUT" --database testdb --profile nonsense >/dev/null 2>&1
expect "unknown profile is a usage error" "$?" "2"

# ---------- the plan, printed before anything is written -------------------------
rm -f "$work/args"
out="$(run "$green" 0 --profile business --dry-run)"
expect "dry run is a dry run"        "$(field "$out" dry_run)" "true"
expect "dry run is ok"               "$(field "$out" ok)"      "true"
expect "dry run exits 0"             "$(rc_of "$green" 0 --profile business --dry-run)" "0"
populate_ran=no; [ -f "$work/args" ] && populate_ran=yes
expect "dry run never invoked populate" "$populate_ran" "no"
expect "resolved order is the post-order one" "$(models "$out")" \
  "res.currency,res.partner,res.users,account.move,account.move.line"
expect "the dependency nobody asked for is named" "$(field "$out" dependency_added)" '["res.currency"]'
expect "a model the database does not have is named" "$(field "$out" unavailable)" '["sale.order"]'
contains "the order is printed, not only returned" "$(cat "$work/stderr")" "resolved model order (5 models"
contains "the dependency is flagged in the printed order" "$(cat "$work/stderr")" "res.currency   [dependency"

# ---------- --exclude ------------------------------------------------------------
# --models does not suppress a dependency, so excluding account.move has to take
# account.move.line with it or account.move gets populated anyway.
out="$(run "$green" 0 --exclude account.move --dry-run)"
expect "the excluded model is gone"  "$(models "$out")" "res.currency,res.partner,res.users"
expect "excluded is reported"        "$(field "$out" excluded)" '["account.move"]'
expect "its dependents go with it"   "$(field "$out" excluded_dependents)" '["account.move.line"]'

out="$(run "$green" 0 --exclude not.a.model --dry-run)"
expect "an exclude that matches nothing is reported, not silent" \
  "$(field "$out" excluded_unknown)" '["not.a.model"]'
expect "and nothing is dropped for it" "$(models "$out")" \
  "res.currency,res.partner,res.users,account.move,account.move.line"

# ---------- a run that completes --------------------------------------------------
out="$(run "$green" 0 --size large)"
expect "a complete run is ok"        "$(field "$out" ok)" "true"
expect "a complete run exits 0"      "$(rc_of "$green" 0 --size large)" "0"
expect "every planned model started" "$(field "$out" models_started)" "5"
expect "nothing is missing"          "$(field "$out" models_not_started)" "[]"
expect "no failing model"            "$(field "$out" failed_model)" "null"
contains "--size is passed straight through" "$(cat "$work/args")" "--size=large"
contains "the curated list is passed as --models" "$(cat "$work/args")" \
  "--models=res.currency,res.partner,res.users,account.move,account.move.line"

expect "rows before is read from postgres" "$(modelfield "$out" res.partner rows_before)" "2"
expect "rows after is read from postgres"  "$(modelfield "$out" res.partner rows_after)"  "102"
expect "rows added is the delta"           "$(modelfield "$out" res.partner rows_added)"  "100"
# 10:00:01,100 -> 10:00:03,100, from the log's own timestamps.
expect "per-model seconds come from the log" "$(modelfield "$out" res.currency seconds)" "2"

# ---------- the swallowed failure, which is the whole point -----------------------
out="$(run "$swallowed" 0)"
expect "a swallowed failure is NOT ok"  "$(field "$out" ok)" "false"
expect "and exits non-zero anyway"      "$(rc_of "$swallowed" 0)" "4"
expect "odoo populate still exited 0"   "$(field "$out" exit_code)" "0"
expect "the failing model is named"     "$(field "$out" failed_model)" '"account.move"'
expect "the aborted tail is named"      "$(field "$out" models_not_started)" '["account.move.line"]'
contains "the swallowed traceback is surfaced" "$(field "$out" traceback)" "custom_reference is required"
contains "and printed, not only returned" "$(cat "$work/stderr")" "POPULATION DID NOT COMPLETE"
contains "with the re-run that skips it" "$(cat "$work/stderr")" "--exclude account.move"

# ---------- fail closed on a log nobody can read ----------------------------------
# An empty log is what a crashed or unrecognized run leaves behind. Zero models
# started is never a pass, whatever the exit code says.
out="$(run "$work/empty.log" 0)"
expect "an unreadable run started nothing" "$(field "$out" models_started)" "0"
expect "zero models started is not a pass" "$(field "$out" ok)" "false"
expect "and it exits non-zero"             "$(rc_of "$work/empty.log" 0)" "4"

# A non-zero exit from odoo itself is a failure even with a clean log.
out="$(run "$green" 1)"
expect "a non-zero odoo exit is not a pass" "$(field "$out" ok)" "false"
expect "and is reported verbatim"           "$(field "$out" exit_code)" "1"

echo "{\"passed\": $pass, \"failed\": $fail}"
[ "$fail" -eq 0 ]
