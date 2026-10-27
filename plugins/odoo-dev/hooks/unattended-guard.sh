#!/usr/bin/env bash
# unattended-guard.sh - PreToolUse hook: refuses the irreversible actions of an
# unattended run. The rules live in lib/unattended-guard.js, which holds the list.
# Input: the PreToolUse payload as JSON on stdin.
# Output: nothing to allow; the deny envelope on stdout to refuse.
# Exit codes: 0 | 2 only if node is unavailable and the guard is engaged.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "${ODOO_DEV_USAGE_LOG:-1}" != 0 ] && command -v usage-log >/dev/null 2>&1; then
  usage-log record hook unattended-guard.sh source=odoo-dev </dev/null >/dev/null 2>&1 || true
fi

command -v node >/dev/null 2>&1 && exec node "$HERE/lib/unattended-guard.js"

# No node: a silent hook would hand an engaged run an unchecked shell, so block.
payload="$(cat)"
if [ -n "${ODOO_UNATTENDED:-}" ] || [[ "$payload" == *odoo-dev-* ]]; then
  echo "unattended-guard.sh: node is unavailable, so the unattended guard cannot run. Denying rather than allowing an unchecked shell." >&2
  exit 2
fi
exit 0
