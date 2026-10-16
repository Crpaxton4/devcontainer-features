#!/usr/bin/env bash
# validate.sh - every CI gate for the odoo-orchestrator plugin.
#
# This plugin has its own validator. plugins/odoo-dev/scripts/validate.sh counts
# the odoo-dev inventory and must not be edited for this plugin.
#
# Env:  REQUIRE_CLAUDE=1  fail, rather than skip, when the claude CLI is absent.
# Exit: 0 every gate that ran passed | 1 at least one gate failed
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fails=0

gate() { printf '\n=== %s\n' "$*"; }
ok() { echo "  PASS $*"; }
bad() {
    echo "  FAIL $*"
    fails=$((fails + 1))
}

gate "manifest and command (claude plugin validate --strict)"
if command -v claude >/dev/null 2>&1; then
    if claude plugin validate "$ROOT" --strict; then ok "plugin validates"; else bad "plugin validate --strict"; fi
elif [ "${REQUIRE_CLAUDE:-0}" = "1" ]; then
    bad "claude CLI is not on PATH and REQUIRE_CLAUDE=1 forbids skipping"
else
    echo "  SKIP claude CLI is not on PATH; the manifest was NOT validated"
fi

gate "shell syntax (bash -n)"
for script in "$ROOT"/scripts/*.sh; do
    if bash -n "$script"; then ok "$(basename "$script")"; else bad "$(basename "$script")"; fi
done

gate "the command names only helpers the plugin ships"
for helper in dispatch.sh orchestrator.py; do
    if [ -x "$ROOT/scripts/$helper" ] && grep -q "scripts/$helper" "$ROOT/commands/run.md"; then
        ok "$helper is executable and named in run.md"
    else
        bad "$helper is missing, not executable, or not named in run.md"
    fi
done

gate "run.md lists every exit code dispatch.sh returns"
# The literal exits of dispatch.sh, plus the dispatcher's own codes it passes through.
codes=$(
    {
        grep -oE 'exit [0-9]+' "$ROOT/scripts/dispatch.sh" | cut -d' ' -f2
        printf '%s\n' 0 1 2 3
    } | sort -un
)
for code in $codes; do
    if grep -qE "^- $code:" "$ROOT/commands/run.md"; then
        ok "exit $code is listed in run.md"
    else
        bad "exit $code is returned by dispatch.sh but is not listed in run.md as '- $code:'"
    fi
done

gate "helper unit tests"
if python3 -m unittest discover -s "$ROOT/scripts/tests" -t "$ROOT/scripts/tests"; then ok "unittest"; else bad "unittest"; fi

echo
if [ "$fails" -eq 0 ]; then echo "VERDICT: all gates passed"; else echo "VERDICT: $fails gate(s) failed"; fi
[ "$fails" -eq 0 ]
