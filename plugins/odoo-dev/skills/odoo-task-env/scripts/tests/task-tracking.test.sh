#!/usr/bin/env bash
# task-tracking.test.sh — offline tests for the odoo-sdk tracking integration:
# existing-work.sh's best-effort "tracking"/"tracking_error" fields and
# worktree-ensure.sh's best-effort registry start_task call.
#
# No network, no docker, no Odoo: `odoo-sdk` is a stub on PATH that logs every
# dispatch and replays canned stdout/exit codes. What is asserted is the degrade
# contract — CLI success filters to this task, CLI failure is a string and never
# fatal, CLI absence never fails the flow — and the exact kwargs the wired
# start_task call sends. The git worktree behaviour itself is task-env.test.sh's
# business and is not re-asserted here.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS="$(cd "$SCRIPT_DIR/.." && pwd)"
work="$(mktemp -d "${TMPDIR:-/tmp}/task-tracking-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
expect() { # expect <label> <actual> <wanted>
  if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted '$3', got '$2'" >&2; fi
}
expect_exit() { # expect_exit <label> <wanted> <actual>
  if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted exit $2, got $3" >&2; fi
}
field() { node -e 'console.log(JSON.stringify(JSON.parse(process.argv[1])[process.argv[2]] ?? null))' "$1" "$2"; }

# ---------- fixture: a repo with a local bare origin --------------------------
origin="$work/origin.git"; repo="$work/checkout"
mkdir -p "$work/no-hooks"
git init --quiet --bare "$origin"
git init --quiet "$repo"
git -C "$repo" config user.email test@example.invalid
git -C "$repo" config user.name "task-tracking test"
git -C "$repo" config core.hooksPath "$work/no-hooks"
git -C "$repo" remote add origin "$origin"
echo seed > "$repo/README"
git -C "$repo" add README
git -C "$repo" commit --quiet -m "seed"
git -C "$repo" branch -M UAT
git -C "$repo" push --quiet -u origin UAT
export REPOS_DIR="" REPOS_DIR_CANDIDATES="$work/nonexistent"
# This suite exists to exercise the tracking integration, so force it on even on
# a machine whose environment pins it off.
export ODOO_TASK_TRACKING=1

# ---------- the odoo-sdk stub -------------------------------------------------
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
reset_stub() { rm -f "$STUB_LOG" "$STUB_DIR"/*; : > "$STUB_LOG"; }
arg_field() { node -e '
  const line = process.argv[1];
  const args = JSON.parse(line.slice(line.indexOf("|") + 1));
  console.log(String(args[process.argv[2]] ?? "null"))
' "$1" "$2"; }

# ---------- existing-work.sh: tracking filters to this task -------------------
reset_stub
echo '[{"run_id":1,"task_id":8100,"state":"RUNNING"},{"run_id":2,"task_id":9999,"state":"RUNNING"}]' \
  > "$STUB_DIR/task_status.json"
out="$(PATH="$stubbin:$PATH" bash "$SCRIPTS/existing-work.sh" anyrepo 8100 UAT --repo-path "$repo" 2>/dev/null | tail -1)"
rc=$?
expect_exit "existing-work with tracking succeeds" 0 $rc
expect "tracking keeps only this task's runs" "$(field "$out" tracking)" '[{"run_id":1,"task_id":8100,"state":"RUNNING"}]'
expect "tracking_error is null on success" "$(field "$out" tracking_error)" "null"
line="$(grep '^task_status|' "$STUB_LOG")"
expect "task_status is dispatched argless" "$line" "task_status|"

# ---------- existing-work.sh: CLI failure degrades, never fatal ---------------
reset_stub
echo '{"error": {"type": "OdooConnectionError", "message": "down"}}' > "$STUB_DIR/task_status.json"
echo 1 > "$STUB_DIR/task_status.rc"
out="$(PATH="$stubbin:$PATH" bash "$SCRIPTS/existing-work.sh" anyrepo 8100 UAT --repo-path "$repo" 2>/dev/null | tail -1)"
rc=$?
expect_exit "tracking failure is not fatal" 0 $rc
expect "tracking degrades to []" "$(field "$out" tracking)" "[]"
case "$(field "$out" tracking_error)" in
  *OdooConnectionError*) pass=$((pass+1)) ;;
  *) fail=$((fail+1)); echo "FAIL tracking_error should carry the CLI envelope: $out" >&2 ;;
esac

# ---------- existing-work.sh: CLI absent degrades, never fatal ----------------
# Only asserted when no real odoo-sdk is installed (CI's script-tests job has
# none); with one on PATH the absent case cannot be simulated honestly.
if ! command -v odoo-sdk >/dev/null 2>&1; then
  out="$(bash "$SCRIPTS/existing-work.sh" anyrepo 8100 UAT --repo-path "$repo" 2>/dev/null | tail -1)"
  rc=$?
  expect_exit "absent CLI is not fatal" 0 $rc
  expect "absent CLI degrades to []" "$(field "$out" tracking)" "[]"
  case "$(field "$out" tracking_error)" in
    *"not installed"*) pass=$((pass+1)) ;;
    *) fail=$((fail+1)); echo "FAIL tracking_error should say the CLI is missing: $out" >&2 ;;
  esac
fi

# ---------- worktree-ensure.sh: start_task wired after worktree success -------
reset_stub
echo '[{"id":9001,"name":"Fix the thing","project_id":[3,"Project X"]}]' > "$STUB_DIR/get_tasks.json"
echo '{"run_id":4,"task_id":9001,"state":"RUNNING","already_running":false}' > "$STUB_DIR/start_task.json"
out="$(PATH="$stubbin:$PATH" bash "$SCRIPTS/worktree-ensure.sh" anyrepo 9001 fix-thing UAT --repo-path "$repo" 2>/dev/null | tail -1)"
rc=$?
expect_exit "worktree with tracking succeeds" 0 $rc
expect "tracking started" "$(field "$out" tracking)" '"started"'
line="$(grep '^start_task|' "$STUB_LOG")"
expect "start_task kwarg task_id"      "$(arg_field "$line" task_id)"      "9001"
expect "start_task kwarg task_name"    "$(arg_field "$line" task_name)"    "Fix the thing"
expect "start_task kwarg project_id"   "$(arg_field "$line" project_id)"   "3"
expect "start_task kwarg project_name" "$(arg_field "$line" project_name)" "Project X"
expect "start_task kwarg branch_name"  "$(arg_field "$line" branch_name)"  "9001-fix-thing"

# Rerun: the worktree is reused and the idempotent start_task reports the
# existing session rather than opening a second one.
reset_stub
echo '[{"id":9001,"name":"Fix the thing","project_id":[3,"Project X"]}]' > "$STUB_DIR/get_tasks.json"
echo '{"run_id":4,"task_id":9001,"state":"RUNNING","already_running": true}' > "$STUB_DIR/start_task.json"
out="$(PATH="$stubbin:$PATH" bash "$SCRIPTS/worktree-ensure.sh" anyrepo 9001 fix-thing UAT --repo-path "$repo" 2>/dev/null | tail -1)"
expect "rerun reuses the worktree" "$(field "$out" status)" '"reused"'
expect "rerun tracking already_running" "$(field "$out" tracking)" '"already_running"'

# ---------- worktree-ensure.sh: every tracking failure warns, never fails -----
reset_stub
echo 1 > "$STUB_DIR/get_tasks.rc"
err="$(PATH="$stubbin:$PATH" bash "$SCRIPTS/worktree-ensure.sh" anyrepo 9002 s UAT --repo-path "$repo" 2>&1 >"$work/out9002")"
rc=$?
out="$(tail -1 "$work/out9002")"
expect_exit "get_tasks failure does not fail the worktree" 0 $rc
expect "get_tasks failure is tracking error" "$(field "$out" tracking)" '"error"'
expect "worktree still created" "$(field "$out" status)" '"created"'
case "$err" in *WARN*) pass=$((pass+1)) ;; *) fail=$((fail+1)); echo "FAIL expected a WARN on stderr: $err" >&2 ;; esac
expect "failed resolve never calls start_task" "$(grep -c '^start_task|' "$STUB_LOG")" "0"

reset_stub
echo '[{"id":9003,"name":"N","project_id":[3,"P"]}]' > "$STUB_DIR/get_tasks.json"
echo '{"error": {"type": "OdooConnectionError", "message": "down"}}' > "$STUB_DIR/start_task.json"
echo 1 > "$STUB_DIR/start_task.rc"
out="$(PATH="$stubbin:$PATH" bash "$SCRIPTS/worktree-ensure.sh" anyrepo 9003 s UAT --repo-path "$repo" 2>/dev/null | tail -1)"
rc=$?
expect_exit "start_task failure does not fail the worktree" 0 $rc
expect "start_task failure is tracking error" "$(field "$out" tracking)" '"error"'

# ---------- worktree-ensure.sh: CLI absent is tracking skipped ----------------
if ! command -v odoo-sdk >/dev/null 2>&1; then
  out="$(bash "$SCRIPTS/worktree-ensure.sh" anyrepo 9004 s UAT --repo-path "$repo" 2>/dev/null | tail -1)"
  rc=$?
  expect_exit "absent CLI does not fail the worktree" 0 $rc
  expect "absent CLI is tracking skipped" "$(field "$out" tracking)" '"skipped"'
fi

echo
echo "task-tracking.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
