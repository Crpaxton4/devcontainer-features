#!/bin/bash

# Executed against the 'on_odoo_base_image' scenario in scenarios.json, which
# installs 'personal-features' on top of the official odoo:19 image.
#
# odoo_sdk is installed as an isolated uv tool (not system-wide) so that the
# Debian-managed cryptography package is never touched. Checks verify the tool
# environment directly rather than system Python.

set -e

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

check "postgresql is ready" pg_isready

check "odoo initializes base module without error" \
    bash -c "odoo -d odoo -i base --stop-after-init --db_host localhost --db_user postgres"

reportResults
