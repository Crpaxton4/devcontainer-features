#!/bin/bash

# Executed against the 'on_odoo16_base_image' scenario in scenarios.json.
# odoo:16 is Debian 11 (bullseye) and ships only Python 3.9.2 - below odoo_sdk's
# requires-python (>=3.10). install.sh used to gate the ENTIRE Python block on
# the base image's python3, so this image silently got no uv, no tool env, no
# odoo-sdk/odoo-mcp/odoo-tui and no mempalace, surfacing weeks later as a
# confusing ENOENT from a stale bind-mounted MCP registration (#674).
#
# Since #674 the venv carries its own uv-managed CPython (pinned in install.sh),
# so the base image's Python is irrelevant and odoo:16 gets the FULL toolchain.
# This scenario is the canonical proof of that interpreter independence: the
# system Python here is the oldest of any supported base image, so if the
# toolchain works here, the pin is doing its job everywhere.

set -e

# shellcheck source=/dev/null  # dev-container-features-test-lib is injected by the test harness at runtime; not resolvable statically. check()/reportResults() come from it.
source dev-container-features-test-lib

# Claude Code: the primary feature output
check "claude is on PATH and executable" bash -c "test -x \"\$(command -v claude)\""
check "claude reports a version" claude --version
check "wrapper injects --ide for default sessions" bash -c "grep -q -- '--ide' \"\$(command -v claude)\""

# Interpreter independence (#674): the base image still ships Python <3.10 -
# this is the precondition that makes the checks below meaningful. If odoo:16
# ever moves to a newer Python this scenario stops being the canonical
# old-Python proof and should be re-pointed at whatever image takes that role.
check "base image still ships the pre-3.10 system Python this scenario exists for" \
    bash -c "python3 -c 'import sys; assert sys.version_info < (3, 10), sys.version'"

# The full toolchain must now be present despite the old system Python.
check "uv is installed" bash -c "command -v uv"
check "odoo-sdk tool env exists" bash -c "test -d /usr/local/share/uv/tools/odoo-sdk"

# The tool env must run the uv-managed pinned interpreter, not the system 3.9
# (which could not even import the SDK's dependency tree).
check "tool env runs the pinned uv-managed CPython 3.11, not the system 3.9 (#674)" \
    bash -c "/usr/local/share/uv/tools/odoo-sdk/bin/python -c '
import sys
assert sys.version_info[:2] == (3, 11), sys.version
'"

# odoo_sdk: installed into the isolated tool environment.
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

# All three console scripts must be linked onto PATH (#496): odoo-sdk is what
# claude-event-hook shells out to, odoo-mcp is what the MCP registration spawns
# (the ENOENT in #674), and odoo-tui is the operator TUI (#120).
check "odoo-sdk console script is on PATH" bash -c "command -v odoo-sdk"
check "odoo-sdk entrypoint is executable" bash -c "test -x \"\$(command -v odoo-sdk)\""
check "odoo-mcp console script is on PATH" bash -c "command -v odoo-mcp"
check "odoo-mcp entrypoint is executable" bash -c "test -x \"\$(command -v odoo-mcp)\""
check "odoo-tui console script is on PATH" bash -c "command -v odoo-tui"
check "odoo-tui entrypoint is executable" bash -c "test -x \"\$(command -v odoo-tui)\""

# System-Python isolation guard: the isolated install must leave the Debian
# packages untouched - odoo:16's own pyOpenSSL must keep importing from the
# system 3.9 exactly as before.
check "system OpenSSL is intact (isolated install didn't touch cryptography)" \
    python3 -c "from OpenSSL import SSL, crypto"

check "postgresql starts and is ready" /usr/local/share/pq-init.sh

check "odoo postgresql role created" \
    bash -c "createuser -U postgres --superuser odoo"

check "odoo initializes base module without error" \
    bash -c "odoo -d odoo -i base --stop-after-init --db_host localhost --db_user odoo"

reportResults
