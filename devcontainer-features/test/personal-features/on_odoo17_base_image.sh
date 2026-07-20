#!/bin/bash

# Executed against the 'on_odoo17_base_image' scenario in scenarios.json.
# odoo:17 ships Python 3.10 — the minimum version required by odoo_sdk — and
# pre-23.2.0 pyOpenSSL that references _lib.X509_V_FLAG_NOTIFY_POLICY. That
# constant does not exist in cryptography 41+ (which uses OpenSSL 3.x CFFI
# bindings), so installing cryptography 41+ system-wide would break odoo:17's
# pyOpenSSL. The isolated install avoids this entirely: system cryptography is
# never touched, and the OpenSSL check must pass to prove that.

set -e

# shellcheck source=/dev/null  # dev-container-features-test-lib is injected by the test harness at runtime; not resolvable statically. check()/reportResults() come from it.
source dev-container-features-test-lib

# Claude Code: the primary feature output
check "claude is on PATH and executable" bash -c "test -x \"\$(command -v claude)\""
check "claude reports a version" claude --version
check "wrapper injects --ide for default sessions" bash -c "grep -q -- '--ide' \"\$(command -v claude)\""

# odoo_sdk: installed into an isolated tool environment.
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

# Python-3.10 TOML config guard. The isolated tool env binds odoo:17's system
# CPython 3.10, where `tomllib` is NOT in the stdlib, and `config.toml` is the
# format probed first in every discovery location. The import checks above only
# reach module scope, so the TOML branch was never taken and a
# ModuleNotFoundError in LocalConfig.load() stayed latent on this exact image.
# These checks force that branch.
ODOO17_CONFIG_DIR="$(mktemp -d)"
cat >"$ODOO17_CONFIG_DIR/config.toml" <<'TOML'
[connection]
url = "https://toml.example.com"
db = "toml-db"
username = "toml-user"
password = "toml-pass"

[behavior]
session_gap_mins = 45
TOML

check "tool env is a pre-3.11 interpreter (no stdlib tomllib)" \
    bash -c "/usr/local/share/uv/tools/odoo-sdk/bin/python -c '
import sys
assert sys.version_info < (3, 11), sys.version
'"

check "LocalConfig parses config.toml without stdlib tomllib" \
    bash -c "ODOO_SDK_CONFIG=\"$ODOO17_CONFIG_DIR\" /usr/local/share/uv/tools/odoo-sdk/bin/python -c '
from odoo_sdk.state.config import LocalConfig

config = LocalConfig.load()
assert config.connection[\"url\"] == \"https://toml.example.com\", config.connection
assert config.connection[\"db\"] == \"toml-db\", config.connection
assert config.session_gap_mins == 45, config.session_gap_mins
'"

check "connection settings resolve from config.toml on Python 3.10" \
    bash -c "ODOO_SDK_CONFIG=\"$ODOO17_CONFIG_DIR\" /usr/local/share/uv/tools/odoo-sdk/bin/python -c '
from odoo_sdk.state.config import LocalConfig

settings = LocalConfig.load().connection_settings()
assert settings.username == \"toml-user\", settings
'"

# System OpenSSL regression guard: this is the critical check for odoo:17.
# If the isolated install accidentally upgraded system cryptography, pre-23.2.0
# pyOpenSSL would fail here with AttributeError on X509_V_FLAG_NOTIFY_POLICY.
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

check "postgresql starts and is ready" /usr/local/share/pq-init.sh

check "odoo postgresql role created" \
    bash -c "createuser -U postgres --superuser odoo"

check "odoo initializes base module without error" \
    bash -c "odoo -d odoo -i base --stop-after-init --db_host localhost --db_user odoo"

reportResults
