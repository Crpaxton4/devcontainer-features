#!/bin/bash

# Executed against the 'on_odoo16_base_image' scenario in scenarios.json.
# odoo:16 ships Python < 3.10, which is below the fastmcp minimum required by
# odoo_sdk. The feature skips the odoo_sdk wheel install on those images but
# must still install all other tooling correctly. This scenario is the test
# analog for any environment with Python < 3.10.

set -e

source dev-container-features-test-lib

# Claude Code: the primary feature output
check "claude is on PATH and executable" bash -c "test -x \"\$(command -v claude)\""
check "claude reports a version" claude --version
check "wrapper injects --ide for default sessions" bash -c "grep -q -- '--ide' \"\$(command -v claude)\""

# odoo_sdk and odoo-mcp are intentionally NOT checked here: Python < 3.10
# means fastmcp cannot be installed, so the feature skips them. Asserting their
# absence would be fragile if the odoo:16 image ever updates its Python version.

reportResults
