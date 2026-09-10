#!/usr/bin/env bash
# writeback.test.sh — offline tests for scripts/writeback.sh.
#
# No network, no Odoo, no SDK install: `odoo-sdk` is a stub on PATH that logs
# every dispatch (name + --args JSON) and replays canned stdout/exit codes from
# a per-command fixture directory. What is asserted is the script's contract:
# argument validation, the 300-character pre-check (client-side UX only — the
# SDK stays authoritative), the exact kwargs each verb sends, and the verbatim
# passthrough of the CLI's JSON error envelope with a non-zero exit.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$HERE/../writeback.sh"
work="$(mktemp -d "${TMPDIR:-/tmp}/writeback-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
expect() { # expect <label> <actual> <wanted>
  if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted '$3', got '$2'" >&2; fi
}
expect_exit() { # expect_exit <label> <wanted> <actual>
  if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted exit $2, got $3" >&2; fi
}
expect_contains() { # expect_contains <label> <haystack> <needle>
  case "$2" in *"$3"*) pass=$((pass+1)) ;; *) fail=$((fail+1)); echo "FAIL $1: '$3' not in '$2'" >&2 ;; esac
}

# ---------- the odoo-sdk stub ---------------------------------------------------
# Logs "<name>|<args json>" per dispatch; replays $STUB_DIR/<name>.json and
# $STUB_DIR/<name>.rc. Unconfigured commands answer {} with exit 0.
stubbin="$work/bin"; STUB_DIR="$work/responses"; STUB_LOG="$work/calls.log"
mkdir -p "$stubbin" "$STUB_DIR"
cat > "$stubbin/odoo-sdk" <<'EOF'
#!/usr/bin/env bash
name="${2:-}"
args=""
[ "${3:-}" = "--args" ] && args="${4:-}"
printf '%s|%s\n' "$name" "$args" >> "$STUB_LOG"
rc=0
[ -f "$STUB_DIR/$name.rc" ] && rc="$(cat "$STUB_DIR/$name.rc")"
if [ -f "$STUB_DIR/$name.json" ]; then cat "$STUB_DIR/$name.json"; else echo '{}'; fi
exit "$rc"
EOF
chmod +x "$stubbin/odoo-sdk"
export STUB_DIR STUB_LOG
PATH="$stubbin:$PATH"
reset_stub() { rm -f "$STUB_LOG" "$STUB_DIR"/*; : > "$STUB_LOG"; }

# arg_field <call-line> <field> — a recorded --args JSON field, stringified.
arg_field() { node -e '
  const line = process.argv[1];
  const args = JSON.parse(line.slice(line.indexOf("|") + 1));
  console.log(String(args[process.argv[2]] ?? "null"));
' "$1" "$2"; }

# ---------- argument validation --------------------------------------------------
bash "$SUT" >/dev/null 2>&1;                          expect_exit "no args is usage"            2 $?
bash "$SUT" frobnicate 1 x >/dev/null 2>&1;           expect_exit "unknown verb is usage"       2 $?
bash "$SUT" note abc "text" >/dev/null 2>&1;          expect_exit "non-numeric task id"         2 $?
bash "$SUT" note 42 >/dev/null 2>&1;                  expect_exit "note without text"           2 $?
bash "$SUT" activity 42 >/dev/null 2>&1;              expect_exit "activity without --summary"  2 $?
bash "$SUT" "done" 42 >/dev/null 2>&1;                  expect_exit "done without --match"        2 $?
bash "$SUT" note 42 hi --bogus x >/dev/null 2>&1;     expect_exit "unknown option is usage"     2 $?

# ---------- odoo-sdk absent ------------------------------------------------------
# A PATH carrying what the script itself needs and no odoo-sdk: the error is the
# CLI envelope shape with a distinct exit code, not a bash command-not-found.
mkdir -p "$work/nosdk"
for b in bash node cat dirname grep; do ln -sf "$(command -v $b)" "$work/nosdk/$b"; done
out="$(PATH="$work/nosdk" "$work/nosdk/bash" "$SUT" note 42 "hello" 2>/dev/null)"
rc=$?
expect_exit "missing CLI exits 4" 4 $rc
expect_contains "missing CLI names MissingCLI" "$out" '"type":"MissingCLI"'

# ---------- note: the 300-character pre-check ------------------------------------
reset_stub
long="$(printf 'x%.0s' $(seq 1 301))"
out="$(bash "$SUT" note 42 "$long" 2>/dev/null)"; rc=$?
expect_exit "301 chars pre-checked" 2 $rc
expect_contains "pre-check names the count" "$out" "301 characters"
expect "pre-check never dispatches" "$(wc -l < "$STUB_LOG" | tr -d ' ')" "0"

# Exactly 300 characters passes the pre-check and dispatches.
reset_stub
edge="$(printf 'x%.0s' $(seq 1 300))"
bash "$SUT" note 42 "$edge" >/dev/null 2>&1
expect_exit "300 chars dispatches" 0 $?
expect "300 chars reaches task_note" "$(grep -c '^task_note|' "$STUB_LOG")" "1"

# ---------- note: kwargs mapping -------------------------------------------------
reset_stub
echo '{"task_name":"T","message_id":77,"note":"PR opened"}' > "$STUB_DIR/task_note.json"
out="$(bash "$SUT" note 42 "PR opened: url" --dedupe-key pr-open-9 2>/dev/null)"; rc=$?
expect_exit "note succeeds" 0 $rc
expect "note passes the CLI result through" "$out" '{"task_name":"T","message_id":77,"note":"PR opened"}'
line="$(grep '^task_note|' "$STUB_LOG")"
expect "note kwarg task_id"    "$(arg_field "$line" task_id)"    "42"
expect "note kwarg note"       "$(arg_field "$line" note)"       "PR opened: url"
expect "note kwarg dedupe_key" "$(arg_field "$line" dedupe_key)" "pr-open-9"

# Without --dedupe-key the kwarg is absent, not empty.
reset_stub
bash "$SUT" note 42 "hi" >/dev/null 2>&1
line="$(grep '^task_note|' "$STUB_LOG")"
expect "no dedupe_key kwarg when not given" "$(arg_field "$line" dedupe_key)" "null"

# ---------- note: CLI error envelope passthrough ---------------------------------
reset_stub
echo '{"error": {"type": "ValueError", "message": "note is too long"}}' > "$STUB_DIR/task_note.json"
echo 1 > "$STUB_DIR/task_note.rc"
out="$(bash "$SUT" note 42 "hi" 2>/dev/null)"; rc=$?
expect_exit "CLI failure passes its exit code through" 1 $rc
expect "CLI failure passes its envelope through verbatim" "$out" \
  '{"error": {"type": "ValueError", "message": "note is too long"}}'

# ---------- activity: type resolution + kwargs -----------------------------------
reset_stub
echo '[{"id":5,"name":"To-Do","res_model":null},{"id":7,"name":"Call","res_model":null}]' \
  > "$STUB_DIR/search_activity_types.json"
echo '{"activity_id":31,"summary":"Review"}' > "$STUB_DIR/schedule_activity.json"
out="$(bash "$SUT" activity 42 --summary "Review PR" --type to-do \
       --note "body" --deadline 2026-09-12 --user-id 8 2>/dev/null)"; rc=$?
expect_exit "activity succeeds" 0 $rc
expect "activity passes the CLI result through" "$out" '{"activity_id":31,"summary":"Review"}'
line="$(grep '^search_activity_types|' "$STUB_LOG")"
expect "type lookup query"     "$(arg_field "$line" query)"     "to-do"
expect "type lookup res_model" "$(arg_field "$line" res_model)" "project.task"
line="$(grep '^schedule_activity|' "$STUB_LOG")"
expect "activity kwarg res_id"        "$(arg_field "$line" res_id)"        "42"
expect "activity kwarg summary"       "$(arg_field "$line" summary)"       "Review PR"
expect "activity resolves the exact type id" "$(arg_field "$line" activity_type)" "5"
expect "activity kwarg note"          "$(arg_field "$line" note)"          "body"
expect "activity kwarg date_deadline" "$(arg_field "$line" date_deadline)" "2026-09-12"
expect "activity kwarg user_id"       "$(arg_field "$line" user_id)"       "8"

# Without --type there is no lookup and no activity_type kwarg.
reset_stub
bash "$SUT" activity 42 --summary "Review PR" >/dev/null 2>&1
expect_exit "typeless activity succeeds" 0 $?
expect "typeless activity skips the lookup" "$(grep -c '^search_activity_types|' "$STUB_LOG")" "0"
line="$(grep '^schedule_activity|' "$STUB_LOG")"
expect "typeless activity sends no activity_type" "$(arg_field "$line" activity_type)" "null"

# Ambiguous (two candidates, no exact match) refuses rather than guesses.
reset_stub
echo '[{"id":5,"name":"To-Do","res_model":null},{"id":7,"name":"Todo Call","res_model":null}]' \
  > "$STUB_DIR/search_activity_types.json"
out="$(bash "$SUT" activity 42 --summary S --type todo 2>/dev/null)"; rc=$?
expect_exit "ambiguous type exits 3" 3 $rc
expect_contains "ambiguous type is named" "$out" "ambiguous"
expect "ambiguous type never schedules" "$(grep -c '^schedule_activity|' "$STUB_LOG")" "0"

# No candidate at all also refuses.
reset_stub
echo '[]' > "$STUB_DIR/search_activity_types.json"
out="$(bash "$SUT" activity 42 --summary S --type nothing 2>/dev/null)"; rc=$?
expect_exit "unknown type exits 3" 3 $rc
expect "unknown type never schedules" "$(grep -c '^schedule_activity|' "$STUB_LOG")" "0"

# ---------- done: match + kwargs -------------------------------------------------
acts='[{"activity_id":11,"summary":"Review PR #9","activity_type":"To-Do"},
       {"activity_id":12,"summary":"Call the client","activity_type":"Call"}]'
reset_stub
printf '%s' "$acts" > "$STUB_DIR/get_activities.json"
echo '{"activity_id":11,"done":true}' > "$STUB_DIR/mark_activity_done.json"
out="$(bash "$SUT" "done" 42 --match "review pr" --feedback "merged" 2>/dev/null)"; rc=$?
expect_exit "done succeeds" 0 $rc
expect "done passes the CLI result through" "$out" '{"activity_id":11,"done":true}'
line="$(grep '^get_activities|' "$STUB_LOG")"
expect "done lists the task activities" "$(arg_field "$line" res_id)" "42"
line="$(grep '^mark_activity_done|' "$STUB_LOG")"
expect "done kwarg activity_id" "$(arg_field "$line" activity_id)" "11"
expect "done kwarg feedback"    "$(arg_field "$line" feedback)"    "merged"

# Zero matches refuses.
reset_stub
printf '%s' "$acts" > "$STUB_DIR/get_activities.json"
out="$(bash "$SUT" "done" 42 --match "no such thing" 2>/dev/null)"; rc=$?
expect_exit "no activity match exits 3" 3 $rc
expect "no match never completes" "$(grep -c '^mark_activity_done|' "$STUB_LOG")" "0"

# Two matches ("e" hits both entries) also refuses — never a guess.
reset_stub
printf '%s' "$acts" > "$STUB_DIR/get_activities.json"
out="$(bash "$SUT" "done" 42 --match "e" 2>/dev/null)"; rc=$?
expect_exit "ambiguous activity match exits 3" 3 $rc
expect_contains "ambiguous match is named" "$out" "more than one"
expect "ambiguous match never completes" "$(grep -c '^mark_activity_done|' "$STUB_LOG")" "0"

# A failing get_activities passes its envelope through before any matching.
reset_stub
echo '{"error": {"type": "OdooConnectionError", "message": "down"}}' > "$STUB_DIR/get_activities.json"
echo 1 > "$STUB_DIR/get_activities.rc"
out="$(bash "$SUT" "done" 42 --match x 2>/dev/null)"; rc=$?
expect_exit "get_activities failure passes through" 1 $rc
expect_contains "get_activities envelope survives" "$out" "OdooConnectionError"

echo
echo "writeback.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
