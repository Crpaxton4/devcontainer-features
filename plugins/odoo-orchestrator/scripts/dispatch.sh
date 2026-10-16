#!/usr/bin/env bash
# dispatch.sh - the one place the orchestrator calls the dispatcher.
#
# Arguments and the exit status pass through unchanged, so the command sees the
# dispatcher's own codes: 0 drained, 1 a job raised, 2 bad arguments or an
# unreadable brief, 3 another dispatcher holds the lock.
#
# Resolution: a `dispatch` on PATH first; otherwise `uv run` against the
# dispatcher project named by ODOO_ORCHESTRATOR_DISPATCHER_DIR (the
# libraries/dispatcher directory of a checkout). Nothing is installed here.
# Exit 127 means neither was found, which the dispatcher never returns.
set -euo pipefail

if command -v dispatch >/dev/null 2>&1; then
    exec dispatch "$@"
fi

if [[ -n ${ODOO_ORCHESTRATOR_DISPATCHER_DIR:-} ]]; then
    exec uv run --project "$ODOO_ORCHESTRATOR_DISPATCHER_DIR" dispatch "$@"
fi

printf 'dispatch.sh: no dispatcher found. Put dispatch on PATH, or set ODOO_ORCHESTRATOR_DISPATCHER_DIR to the libraries/dispatcher directory.\n' >&2
exit 127
