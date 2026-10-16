#!/usr/bin/env bash
# dispatch.sh - the one place the orchestrator calls the dispatcher.
#
# The dispatcher's own exit codes pass through unchanged: 0 drained, 1 a job
# raised, 2 bad arguments or an unreadable brief, 3 another dispatcher holds the
# lock. Exit 127 means no dispatcher was found, which the dispatcher never returns.
#
# `run` blocks until the queue drains, which can take far longer than a foreground
# Bash call lives. Start it in the background and wait for it. A killed run
# dead-letters the jobs it was running. For `run`, stdout is also tee'd to
# <home>/runs/<UTC time>-<pid>.json and that path is printed to stderr first and
# written to <home>/runs/latest.txt, so the result is read from disk. Read the
# file only after the process has exited, and check the exit code first.
#
# Resolution: a `dispatch` on PATH first; otherwise `uv run` against the
# dispatcher project named by ODOO_ORCHESTRATOR_DISPATCHER_DIR (the
# libraries/dispatcher directory of a checkout). Nothing is installed here.
set -uo pipefail

if command -v dispatch >/dev/null 2>&1; then
    cmd=(dispatch)
elif [[ -n ${ODOO_ORCHESTRATOR_DISPATCHER_DIR:-} ]]; then
    cmd=(uv run --project "$ODOO_ORCHESTRATOR_DISPATCHER_DIR" dispatch)
else
    printf 'dispatch.sh: no dispatcher found. Put dispatch on PATH, or set ODOO_ORCHESTRATOR_DISPATCHER_DIR to the libraries/dispatcher directory.\n' >&2
    exit 127
fi

if [[ ${1:-} != run ]]; then
    exec "${cmd[@]}" "$@"
fi

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

"${cmd[@]}" "$@" | tee "$out"
rc=${PIPESTATUS[0]}
# latest.txt names a run that really ran. A run that exits 2 or 3 stored nothing,
# so its empty file is removed and latest.txt keeps pointing at the last real run.
if [[ $rc -eq 0 || $rc -eq 1 ]]; then
    printf '%s\n' "$out" >"$home/runs/latest.txt"
else
    rm -f "$out"
fi
exit "$rc"
