#!/bin/bash

# Executed against the 'on_qoc_prebuilt_16' scenario in scenarios.json: the
# published ghcr.io/qoc-innovations/qoc-devcontainer image for Odoo 16, pulled
# exactly as every Odoo 16 project devcontainer pulls it, with personal-features
# and second-brain layered on top the way those projects layer them.
#
# The build is the real test. odoo:16 is Debian 11 (bullseye), EOL since
# 2026-08-31, and once deb.debian.org tore down its bullseye-security pool the
# first Feature to run apt-get - docker-outside-of-docker, a dependsOn of
# personal-features - failed every Odoo 16 rebuild with "404 Not Found"
# (2026-10-02). test.yaml never saw it: its on_odoo16_base_image fixture repairs
# the apt sources that this image, the one consumers actually run, did not. So
# this scenario deliberately patches nothing - if the image cannot take the
# Features as published, this leg goes red, and the fix belongs in the image.
#
# The checks below only confirm the pieces consumers rely on arrived, with no
# reliance on a database: project devcontainers get theirs from the shared
# compose stack, not from a Feature.

set -e

# shellcheck source=/dev/null  # dev-container-features-test-lib is injected by the test harness at runtime; not resolvable statically. check()/reportResults() come from it.
source dev-container-features-test-lib

# The image under test is the one this scenario claims to cover.
check "image is the Odoo 16 build (ODOO_VERSION=16.x)" \
    bash -c 'case "$ODOO_VERSION" in 16.*) ;; *) echo "ODOO_VERSION=$ODOO_VERSION" >&2; exit 1;; esac'
check "odoo entrypoint is on PATH" bash -c "command -v odoo"

# docker-outside-of-docker: the Feature whose apt-get was first to hit the
# torn-down mirrors. Installed means its apt-get install succeeded.
check "docker CLI is installed (docker-outside-of-docker)" docker --version

# github-cli and node: the other two dependsOn Features that apt-get on install.
check "gh is installed" gh --version
check "node on PATH is >= 18, not the image's own ancient node" \
    bash -c 'node -e "process.exit(parseInt(process.versions.node, 10) >= 18 ? 0 : 1)"'

# personal-features itself: Claude Code plus the odoo-sdk toolchain.
check "claude is on PATH and executable" bash -c "test -x \"\$(command -v claude)\""
check "claude reports a version" claude --version
check "uv is installed" bash -c "command -v uv"
check "odoo-sdk console script is on PATH" bash -c "command -v odoo-sdk"
check "odoo-mcp console script is on PATH" bash -c "command -v odoo-mcp"
check "odoo-tui console script is on PATH" bash -c "command -v odoo-tui"

# second-brain: the host knowledge base is mounted and readable.
check "second brain is mounted with host content" \
    bash -c "grep -q 'host knowledge survives' /mnt/second-brain/host-sentinel.md"

reportResults
