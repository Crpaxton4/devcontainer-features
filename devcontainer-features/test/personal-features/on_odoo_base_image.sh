#!/bin/bash

# Executed against the 'on_odoo_base_image' scenario in scenarios.json, which
# installs 'personal-features' on top of the official odoo:19 image.
#
# odoo_sdk is installed as an isolated uv tool (not system-wide) so that the
# Debian-managed cryptography package is never touched. Checks verify the tool
# environment directly rather than system Python.

set -e

# shellcheck source=/dev/null  # dev-container-features-test-lib is injected by the test harness at runtime; not resolvable statically. check()/reportResults() come from it.
source dev-container-features-test-lib

# Claude Code: the primary feature output
check "claude is on PATH and executable" bash -c "test -x \"\$(command -v claude)\""
check "claude reports a version" claude --version
check "wrapper injects --ide for default sessions" bash -c "grep -q -- '--ide' \"\$(command -v claude)\""

# odoo_sdk: installed into an isolated tool environment.
# Verify via the tool env's own Python, not the system Python.
check "odoo_sdk is importable in tool env" \
    bash -c "/usr/local/share/uv/tools/odoo-sdk/bin/python -c 'import odoo_sdk'"

check "odoo_sdk core API is accessible in tool env" \
    bash -c "/usr/local/share/uv/tools/odoo-sdk/bin/python -c '
from odoo_sdk import (
    OdooClient,
    OdooConnectionSettings,
    OdooExecutor,
    OdooRecordset,
    Domain,
    DomainExpression,
)
'"

# System OpenSSL regression guard: our feature must NOT touch system
# cryptography. If system pyOpenSSL can still import correctly, we know the
# isolated install left the Debian packages untouched.
check "system OpenSSL is intact (isolated install didn't touch cryptography)" \
    python3 -c "from OpenSSL import SSL, crypto"

# The odoo-mcp console script is the primary runtime entry point used in
# devcontainers. Verify it is on PATH and executable.
check "odoo-mcp console script is on PATH" bash -c "command -v odoo-mcp"
check "odoo-mcp entrypoint is executable" bash -c "test -x \"\$(command -v odoo-mcp)\""

# Regression guard for #120: the odoo-tui curses TUI console script must also be
# symlinked onto PATH (it was defined in the SDK but not linked by the feature).
check "odoo-tui console script is on PATH" bash -c "command -v odoo-tui"
check "odoo-tui entrypoint is executable" bash -c "test -x \"\$(command -v odoo-tui)\""

# #369 deliberately reversed the #115-era guard this block used to hold: the
# tracker state is now a HOST-provisioned central DB bind-mounted at
# /usr/local/share/odoo-task-tracker (uid-aligned via updateRemoteUserUID), and
# ODOO_TASK_TRACKER_DIR points there on purpose. Assert the new contract: the
# env var targets the mounted path, the SDK resolves tracker.db under it, and
# a real log-event lands in the mounted DB (CI runs setup.sh on the host
# before the scenario, so the dir and schema exist).
check "ODOO_TASK_TRACKER_DIR points at the host-provisioned tracker mount" bash -c \
    "[ \"\$ODOO_TASK_TRACKER_DIR\" = '/usr/local/share/odoo-task-tracker' ]"
check "odoo_sdk resolves tracker.db under the mounted state root" \
    bash -c "/usr/local/share/uv/tools/odoo-sdk/bin/python -c '
from odoo_sdk.state.db import tracker_db_path

path = tracker_db_path()
assert str(path) == \"/usr/local/share/odoo-task-tracker/tracker.db\", path
'"
check "odoo-sdk log-event writes to the host-provisioned central tracker DB" bash -c \
    "/usr/local/share/uv/tools/odoo-sdk/bin/odoo-sdk log-event --source claude:SessionStart --subject scenario-ci-positive && test -f \"\$ODOO_TASK_TRACKER_DIR/tracker.db\""

# #411: hook<->CLI CONTRACT end to end (mirrors test.sh). The shim swallows every
# failure (always exit 0) and forks the SDK call into a DETACHED background job,
# so a rename of --attach-active-run / --payload or of the `claude:` source prefix
# would drop every event in production while passing grep-only guards. Drive the
# REAL claude-event-hook with a representative PreToolUse payload and assert a row
# LANDED carrying the trimmed --payload and still MATCHING the billing predicate
# that keys on the `claude:` prefix. Self-contained: provisions its OWN throwaway
# tracker DB and points the shim at it via ODOO_TASK_TRACKER_DIR, so it never
# touches the real central DB and runs deterministically. The SDK bin dir is put
# on PATH so the shim's bare `odoo-sdk` resolves to the real installed CLI.
# shellcheck disable=SC2016  # single quotes defer expansion into the check subshell
check "claude-event-hook drives a real PreToolUse event through --attach-active-run/--payload into a provisioned DB" bash -c '
  SDK_BIN=/usr/local/share/uv/tools/odoo-sdk/bin
  test -x "$SDK_BIN/odoo-sdk" || exit 0
  STATE="$(mktemp -d)"
  DB="$STATE/tracker.db"
  # Provision a throwaway central DB with the SDK schema (never the real one).
  "$SDK_BIN/python" -c "import sqlite3, sys; from odoo_sdk.state.db import create_schema; c = sqlite3.connect(sys.argv[1]); create_schema(c); c.commit(); c.close()" "$DB" \
    || { echo "failed to provision temp tracker DB at $DB" >&2; rm -rf "$STATE"; exit 1; }
  subj="scenario-hook-e2e-$$"
  printf "{\"session_id\":\"s-%s\",\"tool_name\":\"%s\",\"hook_event_name\":\"PreToolUse\",\"cwd\":\"/tmp\"}" "$$" "$subj" \
    | ODOO_TASK_TRACKER_DIR="$STATE" PATH="$SDK_BIN:$PATH" /usr/local/bin/claude-event-hook PreToolUse
  # The shim fires the SDK write in a DETACHED background job, so poll the DB.
  for _ in $(seq 1 50); do
    "$SDK_BIN/python" - "$DB" "$subj" <<PY && { rm -rf "$STATE"; exit 0; }
import json, sqlite3, sys
from odoo_sdk.state.db import _DEVELOPMENT_SOURCE_PREDICATE
db, subj = sys.argv[1], sys.argv[2]
conn = sqlite3.connect(db)
row = conn.execute(
    f"SELECT source, payload FROM events WHERE subject = ? AND {_DEVELOPMENT_SOURCE_PREDICATE}",
    (subj,),
).fetchone()
if row is None:
    sys.exit(1)
source, payload = row
assert source == "claude:PreToolUse", source
assert json.loads(payload)["tool_name"] == subj, payload
sys.exit(0)
PY
    sleep 0.2
  done
  echo "no billing-eligible claude:PreToolUse row for subject $subj ever landed" >&2
  rm -rf "$STATE"
  exit 1
'

check "postgresql starts and is ready" /usr/local/share/pq-init.sh

check "odoo postgresql role created" \
    bash -c "createuser -U postgres --superuser odoo"

check "odoo initializes base module without error" \
    bash -c "odoo -d odoo -i base --stop-after-init --db_host localhost --db_user odoo"

reportResults
