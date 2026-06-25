#!/bin/bash

# Executed against the 'on_odoo_base_image' scenario in scenarios.json, which
# installs 'personal-features' on top of the official odoo:19 image. The Odoo
# base image ships several Python packages (typing_extensions, cryptography,
# etc.) via Debian's package manager, which lack pip RECORD files and would
# block an ordinary 'pip install' from upgrading them. This scenario verifies
# that the feature installs correctly in that environment and that the bundled
# odoo_sdk wheel is fully functional - not just present.

set -e

source dev-container-features-test-lib

# Claude Code: the primary feature output
check "claude is on PATH and executable" bash -c "test -x \"\$(command -v claude)\""
check "claude reports a version" claude --version
check "wrapper injects --ide for default sessions" bash -c "grep -q -- '--ide' \"\$(command -v claude)\""

# odoo_sdk Python package: verify the full import succeeds without errors from
# dependency version conflicts (the odoo:19 image ships older Debian packages
# that pip cannot uninstall, and --ignore-installed must be in place for this
# to work). A clean import is the real integration signal.
check "odoo_sdk package is importable" python3 -c "import odoo_sdk"

# Verify the public API is reachable end-to-end: if any transitive dependency
# (pydantic, fastmcp, etc.) failed to install correctly, these imports will
# raise ImportError or AttributeError at class-definition time.
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

# Regression guard: cryptography must stay in the range pyOpenSSL 23.2.0
# (shipped by Ubuntu Noble / odoo:19) supports (<43). cryptography >=43
# removes _lib.GEN_EMAIL, which pyOpenSSL 23.2.0 references at import time.
check "OpenSSL is importable (pyopenssl/cryptography version compat)" \
    python3 -c "from OpenSSL import SSL, crypto"

# The odoo-mcp console script is the primary runtime entry point used in
# devcontainers. Verify it is on PATH and executable. (The command validates
# Odoo connection settings at startup before processing any flags, so it
# cannot be invoked meaningfully without a live config in this test context.)
check "odoo-mcp console script is on PATH" bash -c "command -v odoo-mcp"
check "odoo-mcp entrypoint is executable" bash -c "test -x \"\$(command -v odoo-mcp)\""

reportResults
