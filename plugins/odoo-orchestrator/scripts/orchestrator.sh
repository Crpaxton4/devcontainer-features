#!/usr/bin/env bash
# orchestrator.sh - runs orchestrator.py in the `orchestrator` container of the
# compose stack in stack/compose.yaml, so the host needs Docker and nothing else.
#
#   orchestrator.sh <subcommand> [args...]
#
# The container runs the orchestrator.py that sits beside this script, mounted
# read-only at the same path, so the command and its helper never differ. Standard
# input passes through (`unhandled` reads it). The container has no docker socket.
# It sees the orchestrator home (read-write) and the artifacts dir (read-only).
#
# Exit codes are those of orchestrator.py: 0 done, 1 `lint` found a hit, 2 bad
# arguments or unreadable input. This script adds 127 when
# ODOO_ORCHESTRATOR_CHECKOUT is unset or the stack file was not found.
set -uo pipefail

if [[ -z ${ODOO_ORCHESTRATOR_CHECKOUT:-} ]]; then
    printf 'orchestrator.sh: ODOO_ORCHESTRATOR_CHECKOUT is not set. Set it to the checkout that holds stack/compose.yaml.\n' >&2
    exit 127
fi
stack_file=$ODOO_ORCHESTRATOR_CHECKOUT/stack/compose.yaml
if [[ ! -f $stack_file ]]; then
    printf 'orchestrator.sh: %s not found. Check ODOO_ORCHESTRATOR_CHECKOUT.\n' "$stack_file" >&2
    exit 127
fi

scripts_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# Compose reads neither bash's UID nor a GID, so pass the numbers it needs. The
# docker group id is needed to read the compose file, not by this service.
export ODOO_ORCHESTRATOR_CHECKOUT
export ODOO_ORCHESTRATOR_SCRIPTS_DIR=$scripts_dir
: "${ODOO_ORCHESTRATOR_UID:=$(id -u)}"
: "${ODOO_ORCHESTRATOR_GID:=$(id -g)}"
if [[ -z ${ODOO_ORCHESTRATOR_DOCKER_GID:-} ]]; then
    ODOO_ORCHESTRATOR_DOCKER_GID=$(stat -c %g /var/run/docker.sock 2>/dev/null || true)
fi
export ODOO_ORCHESTRATOR_UID ODOO_ORCHESTRATOR_GID ODOO_ORCHESTRATOR_DOCKER_GID
# A missing bind source would be created by the daemon, owned by root.
mkdir -p "${DISPATCH_ARTIFACTS_DIR:-$HOME/.config/odoo-dev/dispatch/artifacts}"
mkdir -p "${ODOO_ORCHESTRATOR_HOME:-$HOME/.local/state/odoo-orchestrator}"

exec docker compose -f "$stack_file" run --rm -T orchestrator \
    python3 "$scripts_dir/orchestrator.py" "$@"
