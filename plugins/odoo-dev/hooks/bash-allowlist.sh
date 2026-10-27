#!/usr/bin/env bash
# bash-allowlist.sh - PreToolUse hook: odoo-dev-tester is read-only for real. The
# allowlist lives in lib/bash-allowlist.js. It engages only for agent_type odoo-dev-tester.
# Input: the PreToolUse payload as JSON on stdin.
# Output: nothing to allow; the deny envelope on stdout to refuse.
# Exit codes: 0 | 2 only if node is unavailable and the payload names the tester.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "${ODOO_DEV_USAGE_LOG:-1}" != 0 ] && command -v usage-log >/dev/null 2>&1; then
  usage-log record hook bash-allowlist.sh source=odoo-dev </dev/null >/dev/null 2>&1 || true
fi

command -v node >/dev/null 2>&1 && exec node "$HERE/lib/bash-allowlist.js"

# No node: a silent hook would hand the tester an unrestricted shell, so block.
payload="$(cat)"
if [[ "$payload" == *odoo-dev-tester* ]]; then
  echo "bash-allowlist.sh: node is unavailable, so the odoo-dev-tester command allowlist cannot run. Denying rather than allowing an unchecked shell." >&2
  exit 2
fi
exit 0
