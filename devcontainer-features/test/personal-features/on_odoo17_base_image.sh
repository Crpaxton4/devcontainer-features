#!/bin/bash

# Executed against the 'on_odoo17_base_image' scenario in scenarios.json.
# odoo:17 ships Python 3.10 — the minimum version required by odoo_sdk (fastmcp
# lower bound). This scenario is the canonical test for the Python 3.10 baseline:
# odoo_sdk must install and import correctly, and install.sh must handle the
# absence of --break-system-packages (pip <22.1) via its runtime capability check.

set -e

source dev-container-features-test-lib

# Claude Code: the primary feature output
check "claude is on PATH and executable" bash -c "test -x \"\$(command -v claude)\""
check "claude reports a version" claude --version
check "wrapper injects --ide for default sessions" bash -c "grep -q -- '--ide' \"\$(command -v claude)\""

# odoo_sdk Python package: verify the full import succeeds without errors from
# dependency version conflicts.
check "odoo_sdk package is importable" python3 -c "import odoo_sdk"

# Verify the public API is reachable end-to-end.
check "odoo_sdk core API is accessible" python3 -c "
from odoo_sdk import (
    OdooClient,
    OdooConnectionSettings,
    OdooExecutor,
    OdooRecordset,
    Domain,
    DomainExpression,
)
"

# NOTE: the OpenSSL compat check present in the odoo:18/19 test scripts is
# intentionally omitted here. odoo:17 ships a pre-23.2.0 pyOpenSSL that
# references _lib.X509_V_FLAG_NOTIFY_POLICY, a constant not exposed by any
# modern cryptography build. That is a base-image incompatibility unrelated to
# our SDK; the cryptography<43 pin is only relevant for pyOpenSSL >=23.2.0.

# The odoo-mcp console script is the primary runtime entry point used in
# devcontainers. Verify it is on PATH and executable.
check "odoo-mcp console script is on PATH" bash -c "command -v odoo-mcp"
check "odoo-mcp entrypoint is executable" bash -c "test -x \"\$(command -v odoo-mcp)\""

reportResults
