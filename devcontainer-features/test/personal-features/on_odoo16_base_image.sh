#!/bin/bash

# Executed against the 'on_odoo16_base_image' scenario in scenarios.json.
# odoo:16 ships Python < 3.10, below the fastmcp minimum required by odoo_sdk.
# install.sh detects this at runtime and skips the wheel installation entirely.
# This scenario is the canonical test for any Python <3.10 environment.
#
# Checks are split into two groups:
#   SHOULD WORK  — the feature must install these regardless of Python version.
#   MUST BE ABSENT — confirms install.sh correctly skipped the SDK components;
#                    if these pass when they should fail, a regression has crept in.

set -e

source dev-container-features-test-lib

# --- SHOULD WORK on every image -----------------------------------------------

check "claude is on PATH and executable" bash -c "test -x \"\$(command -v claude)\""
check "claude reports a version" claude --version
check "wrapper injects --ide for default sessions" bash -c "grep -q -- '--ide' \"\$(command -v claude)\""

# --- MUST BE ABSENT on Python <3.10 -------------------------------------------
# Positive confirmation that install.sh correctly skipped the SDK components.
# If odoo_sdk were accidentally installed (e.g. the Python version guard broke),
# these checks would fail and alert us to the regression.

check "odoo_sdk was not installed (Python <3.10 guard)" \
    bash -c "! python3 -c 'import odoo_sdk' 2>/dev/null"

check "odoo-mcp was not installed (Python <3.10 guard)" \
    bash -c "! command -v odoo-mcp 2>/dev/null"

reportResults
