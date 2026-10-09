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
# file before the container starts. (All ten sources are directories today.)
#
# One row is host-provisioned (provision=host): the odoo-sdk tracker database
# directory. For it this script also initializes the SQLite schema via
# scripts/init_tracker_db.py, because the container never creates that DB (#369).
#
# Safe to re-run: mkdir -p / touch are no-ops when the targets already exist,
# and chmod just re-asserts the manifest's mode column (0700 for the
# credential-holding dirs - they hold e.g. ~/.claude/.credentials.json and
# gh's hosts.yml, and the container sees the host mode through the mount).
#
# It does NOT chown anything, and it refuses to continue over a source it does
# not own: see assert_owned below (#974).

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

# path_owner PATH - the numeric uid that owns PATH, or empty when that cannot be
# determined (PATH absent, or no stat that answers). GNU coreutils spells this
# `stat -c %u` and BSD/macOS stat spells it `stat -f %u`; neither accepts the
# other's flag and this script runs on Linux, WSL and macOS, so try both. An
# undeterminable owner skips the check below rather than guessing at one.
path_owner() {
    stat -c '%u' "$1" 2>/dev/null || stat -f '%u' "$1" 2>/dev/null || true
}

# assert_owned PATH NAME - stop, with the remedy, when PATH already exists and
# belongs to somebody else (#974).
#
# Without this the script simply dies on the `chmod` below with
# `chmod: changing permissions of '/home/you/.coderabbit': Operation not
# permitted` and, under `set -eu`, nothing else - no path, no cause, no fix.
# That is not a hypothetical: Docker does not refuse a bind mount whose source
# is missing, it CREATES the source, as `root:root 0755`. So a row added to the
# manifest without a re-run of this script here leaves a root-owned directory on
# the host, the container mounts it and looks healthy while being unable to
# write a byte through it, and the first thing that reports anything at all is
# this chmod - on the next re-run, with that one opaque line.
#
# Nothing is chowned automatically: this script deliberately runs unprivileged
# (it only ever touches paths under $HOME) and acquiring root to repair a
# directory the user never asked for is not its call to make.
assert_owned() {
    _ao_owner="$(path_owner "$1")"
    # Absent, or an owner this platform will not report: nothing to assert.
    # `if`, not `&& return`, so this reads the same under `set -e` however it is
    # called - an AND-OR list whose test fails is the one shape where errexit's
    # behaviour depends on the caller's context.
    if [ -z "$_ao_owner" ] || [ "$_ao_owner" = "$(id -u)" ]; then
        return 0
    fi
    # root may chmod anything, so there is no failure ahead to pre-empt.
    if [ "$(id -u)" = 0 ]; then
        return 0
    fi
    echo "ERROR: $1 exists but is owned by uid $_ao_owner, not you (uid $(id -u))." >&2
    echo "       This is the '$2' row of persisted-paths.tsv, and the chmod below would die" >&2
    echo "       on it with a bare 'Operation not permitted'." >&2
    if [ "$_ao_owner" = 0 ]; then
        echo "       uid 0 is the tell: this script never produces a root-owned source, so Docker" >&2
        echo "       created this directory itself, as root:root, when a container bind-mounted it" >&2
        echo "       before this script had ever created it - which means the mount is present" >&2
        echo "       inside the container and nothing there can write through it (#974)." >&2
    else
        echo "       It belongs to another user, so a container mounting it runs as neither its" >&2
        echo "       owner nor root and cannot write through the mount (#974)." >&2
    fi
    echo "       Take it back, then re-run this script:" >&2
    echo "" >&2
    echo "         sudo chown -R $(id -u):$(id -g) $1" >&2
    echo "         ./setup.sh" >&2
    echo "" >&2
    return 1
}

# Host-provisioned state directory (provision=host in the manifest), captured
# during the loop so the tracker-database schema init below is manifest-derived
# rather than a hardcoded path. Empty when no host row is present.
TRACKER_DIR=""

TAB="$(printf '\t')"
while IFS="$TAB" read -r _name _host_source _container_target _env_var _env_value _mode _provision; do
    case "$_name" in ''|'#'*) continue ;; esac  # skip blank/comment lines
    # Before mkdir/chmod, not after: a source somebody else owns is exactly what
    # the chmod below cannot repair, and the point is to say so rather than to
    # die on it (#974).
    assert_owned "$HOME/$_host_source" "$_name" || exit 1
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
