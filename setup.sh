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
# file before the container starts. (All seven sources are directories today.)
#
# One row is host-provisioned (provision=host): the odoo-sdk tracker database
# directory. For it this script also initializes the SQLite schema via
# scripts/init_tracker_db.py, because the container never creates that DB (#369).
#
# Safe to re-run: mkdir -p / touch are no-ops when the targets already exist,
# and chmod just re-asserts the manifest's mode column (0700 for the
# credential-holding dirs - they hold e.g. ~/.claude/.credentials.json and
# gh's hosts.yml, and the container sees the host mode through the mount).

set -eu

: "${HOME:?HOME must be set}"

# Locate the manifest relative to this script so it works from any CWD.
SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
MANIFEST="$SCRIPT_DIR/devcontainer-features/src/personal-features/persisted-paths.tsv"
[ -f "$MANIFEST" ] || {
    echo "ERROR: persisted-paths manifest not found at $MANIFEST" >&2
    echo "Run this script from a checkout of the devcontainer-features repo." >&2
    exit 1
}

# Host-provisioned state directory (provision=host in the manifest), captured
# during the loop so the tracker-database schema init below is manifest-derived
# rather than a hardcoded path. Empty when no host row is present.
TRACKER_DIR=""

TAB="$(printf '\t')"
while IFS="$TAB" read -r _name _host_source _container_target _env_var _env_value _mode _provision; do
    case "$_name" in ''|'#'*) continue ;; esac  # skip blank/comment lines
    case "$_host_source" in
        */) mkdir -p "$HOME/$_host_source" ;;
        *)  mkdir -p "$(dirname "$HOME/$_host_source")"; touch "$HOME/$_host_source" ;;
    esac
    # Enforce the manifest's mode on every run, not just on creation (#233).
    # These are the dirs the container bind-mounts, so with the mounts active
    # the HOST mode is what the container sees - the credential dirs (0700 in
    # the manifest) must not be world-readable here for the container-side
    # hardening to mean anything.
    chmod "$_mode" "$HOME/$_host_source"
    printf 'ok  %s\n' "$HOME/$_host_source"
    case "$_provision" in host) TRACKER_DIR="$HOME/$_host_source" ;; esac
done < "$MANIFEST"

# Initialize the host-provisioned tracker database schema (#369). The odoo-sdk
# tracker DB is a single per-user SQLite file that is bind-mounted into every
# container; the SDK inside the container deliberately never creates it (a
# self-created DB would be container-local and discarded on rebuild), so the
# schema must exist on the host before the first container starts. The init
# script is stdlib-only Python - idempotent, safe to re-run.
if [ -n "$TRACKER_DIR" ]; then
    INIT_SCRIPT="$SCRIPT_DIR/scripts/init_tracker_db.py"
    if ! command -v python3 >/dev/null 2>&1; then
        echo "ERROR: python3 is required to initialize the tracker database" >&2
        echo "schema at ${TRACKER_DIR}tracker.db. Install Python 3 and re-run." >&2
        exit 1
    fi
    python3 "$INIT_SCRIPT" "${TRACKER_DIR}tracker.db"
fi
