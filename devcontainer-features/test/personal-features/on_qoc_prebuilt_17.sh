#!/bin/bash

# Executed against the 'on_qoc_prebuilt_17' scenario in scenarios.json: the
# published ghcr.io/qoc-innovations/qoc-devcontainer image for Odoo 17, pulled
# exactly as every Odoo 17 project devcontainer pulls it, with personal-features
# and second-brain layered on top the way those projects layer them.
#
# The build is the real test; see on_qoc_prebuilt_16.sh for why these legs
# exist and why they patch nothing. odoo:17 is Debian 12 (bookworm), which ages
# out of LTS around 2028 - when it does, this is the leg that says so.

set -e

# shellcheck source=/dev/null  # dev-container-features-test-lib is injected by the test harness at runtime; not resolvable statically. check()/reportResults() come from it.
source dev-container-features-test-lib

check "image is the Odoo 17 build (ODOO_VERSION=17.x)" \
    bash -c 'case "$ODOO_VERSION" in 17.*) ;; *) echo "ODOO_VERSION=$ODOO_VERSION" >&2; exit 1;; esac'
check "odoo entrypoint is on PATH" bash -c "command -v odoo"

check "docker CLI is installed (docker-outside-of-docker)" docker --version
check "gh is installed" gh --version
check "node on PATH is >= 18, not the image's own ancient node" \
    bash -c 'node -e "process.exit(parseInt(process.versions.node, 10) >= 18 ? 0 : 1)"'

check "claude is on PATH and executable" bash -c "test -x \"\$(command -v claude)\""
check "claude reports a version" claude --version
check "uv is installed" bash -c "command -v uv"
check "odoo-sdk console script is on PATH" bash -c "command -v odoo-sdk"
check "odoo-mcp console script is on PATH" bash -c "command -v odoo-mcp"
check "odoo-tui console script is on PATH" bash -c "command -v odoo-tui"

check "second brain is mounted with host content" \
    bash -c "grep -q 'host knowledge survives' /mnt/second-brain/host-sentinel.md"

reportResults
