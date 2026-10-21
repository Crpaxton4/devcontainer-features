#!/usr/bin/env bash
# dispatch.sh - the one place the orchestrator calls the dispatcher.
#
# The dispatcher runs in a container of the compose stack in stack/compose.yaml.
# ODOO_ORCHESTRATOR_CHECKOUT names the checkout that holds that file. This script
# is a front end for `docker compose`; it needs Docker and compose on the host and
# nothing else.
#
# The dispatcher's own exit codes pass through unchanged: 0 drained, 1 a job
# raised, 2 bad arguments, bad settings or an unreadable brief, 3 another
# dispatcher holds the lock. This script adds two codes the dispatcher never
# returns: 74 when it cannot create the orchestrator home, and 127 when the stack
# file was not found. `add` also exits 2 when it gets no JSON argument.
#
#   add '<json>'  submit the jobs, print the submit result on stdout, start the
#                 dispatcher container in the background, then check for a short
#                 time that a PENDING job did not lose a race with an exiting
#                 container. It does not wait for the jobs.
#   run ...       blocks until the queue drains, which can take far longer than a
#                 foreground Bash call lives. Start it in the background and wait
#                 for it. A killed run dead-letters the jobs it was running.
#                 stdout is also tee'd to <home>/runs/<UTC time>-<pid>.json and
#                 that path is printed to stderr first and written to
#                 <home>/runs/latest.txt, so the result is read from disk. Read
#                 the file only after the process has exited, and check the exit
#                 code first.
#   anything else (report, dead-letters, ...) runs `dispatch` with those arguments.
set -uo pipefail

if [[ -z ${ODOO_ORCHESTRATOR_CHECKOUT:-} ]]; then
    printf 'dispatch.sh: ODOO_ORCHESTRATOR_CHECKOUT is not set. Set it to the checkout that holds stack/compose.yaml.\n' >&2
    exit 127
fi
stack_file=$ODOO_ORCHESTRATOR_CHECKOUT/stack/compose.yaml
if [[ ! -f $stack_file ]]; then
    printf 'dispatch.sh: %s not found. Check ODOO_ORCHESTRATOR_CHECKOUT.\n' "$stack_file" >&2
    exit 127
fi

# Compose reads neither bash's UID nor a GID, so pass the numbers it needs.
export ODOO_ORCHESTRATOR_CHECKOUT
: "${ODOO_ORCHESTRATOR_UID:=$(id -u)}"
: "${ODOO_ORCHESTRATOR_GID:=$(id -g)}"
if [[ -z ${ODOO_ORCHESTRATOR_DOCKER_GID:-} ]]; then
    ODOO_ORCHESTRATOR_DOCKER_GID=$(stat -c %g /var/run/docker.sock 2>/dev/null || true)
fi
export ODOO_ORCHESTRATOR_UID ODOO_ORCHESTRATOR_GID ODOO_ORCHESTRATOR_DOCKER_GID
# A missing bind source would be created by the daemon, owned by root.
mkdir -p "$HOME/.local/state/usage-log"
mkdir -p "${DISPATCH_ARTIFACTS_DIR:-$HOME/.config/odoo-dev/dispatch/artifacts}"

compose=(docker compose -f "$stack_file")
dispatch=("${compose[@]}" run --rm -T dispatcher dispatch)

# Seconds the add step watches for a lost race, and the gap between looks.
check_window_s=30
check_step_s=5

dispatcher_running() {
    [[ -n $("${compose[@]}" ps --status running -q dispatcher 2>/dev/null) ]]
}

add_jobs() {
    if [[ $# -ne 1 ]]; then
        printf "usage: dispatch.sh add '<json array>'\n" >&2
        return 2
    fi
    local out rc batch_id waited=0
    out=$("${dispatch[@]}" submit "$1")
    rc=$?
    if [[ $rc -ne 0 ]]; then
        return "$rc"
    fi
    printf '%s\n' "$out"
    "${compose[@]}" up -d dispatcher webui >&2 || return $?
    batch_id=$(printf '%s\n' "$out" | sed -n 's/.*"batch_id": *"\([0-9a-f]*\)".*/\1/p' | head -n 1)
    if [[ -z $batch_id ]]; then
        return 0
    fi
    # The container can exit between its last look at the queue and the submit
    # above. If it is not running and a job is still PENDING, start it once more.
    while [[ $waited -lt $check_window_s ]]; do
        sleep "$check_step_s"
        waited=$((waited + check_step_s))
        if dispatcher_running; then
            continue
        fi
        if "${dispatch[@]}" report "$batch_id" 2>/dev/null | grep -q '"state": *"PENDING"'; then
            "${compose[@]}" up -d dispatcher >&2
        fi
        break
    done
    return 0
}

case ${1:-} in
    add)
        shift
        add_jobs "$@"
        exit $?
        ;;
    run) ;;
    *)
        exec "${dispatch[@]}" "$@"
        ;;
esac

home=${ODOO_ORCHESTRATOR_HOME:-$HOME/.local/state/odoo-orchestrator}
tilde='~'  # a variable, so the patterns below match a literal tilde
case $home in
    "$tilde") home=$HOME ;;
    "$tilde"/*) home=$HOME/${home#"$tilde"/} ;;
    /*) ;;
    *) home=$PWD/$home ;;
esac
if ! mkdir -p "$home/runs"; then
    printf 'dispatch.sh: cannot create %s/runs\n' "$home" >&2
    exit 74
fi
out="$home/runs/$(date -u +%Y%m%dT%H%M%SZ)-$$.json"
printf 'dispatch.sh: result file: %s\n' "$out" >&2

"${dispatch[@]}" "$@" | tee "$out"
rc=${PIPESTATUS[0]}
# latest.txt names a run that really ran. A run that exits with any code other
# than 0 or 1 stored nothing, so its empty file is removed and latest.txt keeps
# pointing at the last real run.
if [[ $rc -eq 0 || $rc -eq 1 ]]; then
    printf '%s\n' "$out" >"$home/runs/latest.txt"
else
    rm -f "$out"
fi
exit "$rc"
