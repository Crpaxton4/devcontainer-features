#!/bin/bash

# Executed against the 'on_odoo16_base_image' scenario in scenarios.json, which
# installs 'personal-features' on top of the official odoo:16 image. Verifies
# that the feature installs correctly on odoo:16 and that the bundled odoo_sdk
# wheel is fully functional.

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

# Regression guard: cryptography must stay in the range pyOpenSSL supports (<43).
check "OpenSSL is importable (pyopenssl/cryptography version compat)" \
    python3 -c "from OpenSSL import SSL, crypto"

# The odoo-mcp console script is the primary runtime entry point used in
# devcontainers. Verify it is on PATH and executable.
check "odoo-mcp console script is on PATH" bash -c "command -v odoo-mcp"
check "odoo-mcp entrypoint is executable" bash -c "test -x \"\$(command -v odoo-mcp)\""

reportResults
