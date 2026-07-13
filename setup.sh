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
# The paths come from persisted-paths.tsv, the single source of truth shared with
# the Feature's install.sh and devcontainer-feature.json. Editing that manifest
# is the only place a persisted path is added. setup.ps1 mirrors this list for
# Windows hosts, and .github/scripts/test_host_setup_parity.py enforces that the
# two scripts and the Feature JSON agree.
#
# A trailing slash in the manifest marks a DIRECTORY source; no trailing slash
# marks a FILE source, which is created with touch after its parent dir. That
# distinction matters: Docker materialises a *missing* single-file mount source
# as a directory, which then fails the mount, so a file source must exist as a
# file before the container starts. (All six sources are directories today.)
#
# Safe to re-run: mkdir -p / touch are no-ops when the targets already exist,
# and chmod just re-asserts the manifest's mode column (0700 for the
# credential-holding dirs - they hold e.g. ~/.claude/.credentials.json and
# gh's hosts.yml, and the container sees the host mode through the mount).

set -eu

: "${HOME:?HOME must be set}"

# Locate the manifest relative to this script so it works from any CWD.
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
MANIFEST="$SCRIPT_DIR/devcontainer-features/src/personal-features/persisted-paths.tsv"
[ -f "$MANIFEST" ] || {
    echo "ERROR: persisted-paths manifest not found at $MANIFEST" >&2
    echo "Run this script from a checkout of the devcontainer-features repo." >&2
    exit 1
}

TAB="$(printf '\t')"
while IFS="$TAB" read -r name host_source container_target env_var env_value mode; do
    case "$name" in ''|'#'*) continue ;; esac  # skip blank/comment lines
    case "$host_source" in
        */) mkdir -p "$HOME/$host_source" ;;
        *)  mkdir -p "$(dirname "$HOME/$host_source")"; touch "$HOME/$host_source" ;;
    esac
    # Enforce the manifest's mode on every run, not just on creation (#233).
    # These are the dirs the container bind-mounts, so with the mounts active
    # the HOST mode is what the container sees - the credential dirs (0700 in
    # the manifest) must not be world-readable here for the container-side
    # hardening to mean anything.
    chmod "$mode" "$HOME/$host_source"
    printf 'ok  %s\n' "$HOME/$host_source"
done < "$MANIFEST"
