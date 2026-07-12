#!/bin/sh
# One-time host setup for personal-features bind mounts (Linux, WSL, macOS).
# On a native Windows host, run setup.ps1 from PowerShell instead.
#
# Run this once per machine before starting any dev container that uses the
# personal-features Feature. It creates the host-side paths that are bind-mounted
# into the container. The tools may not be installed locally (they're only used
# inside dev containers), so their config dirs may not exist yet.
#
# This is NOT optional: a bind mount whose source doesn't exist is a hard
# container-create failure ("bind source path does not exist"), not a fallback.
#
# The list below must match the `mounts` in
# devcontainer-features/src/personal-features/devcontainer-feature.json, which is
# the source of truth. .github/scripts/test_host_setup_parity.py enforces that
# this script, setup.ps1 and that file agree.
#
# Safe to re-run: mkdir -p is a no-op when the targets already exist.

set -eu

: "${HOME:?HOME must be set}"

mkdir -p \
    "$HOME/.claude" \
    "$HOME/.config/gh" \
    "$HOME/.config/odoo_sdk" \
    "$HOME/.config/pr-automation/projects" \
    "$HOME/.config/coderabbit" \
    "$HOME/.config/devcontainer/shell-history"
