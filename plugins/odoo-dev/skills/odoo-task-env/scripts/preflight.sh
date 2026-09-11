#!/usr/bin/env bash
# preflight.sh — one-shot environment gate for the delivery suite.
#
# Usage: preflight.sh [--soft]
#   --soft: report failures but exit 0.
#
# Runs ALL checks rather than stopping at the first, because the useful answer is
# "these three things are wrong", not "the first thing is wrong" three times in a
# row. PASS/FAIL per check on stderr, one JSON object as the last stdout line.
#
# Context-aware, because the requirements genuinely differ:
#   host          docker, a populated repos tree, gh, and the devcontainer CLI —
#                 this is where worktrees and stacks are managed from
#   devcontainer  odoo and a reachable postgres; docker and the repos tree are
#                 neither present nor needed
# The lifted original only knew the host case and reported four confident
# failures inside a perfectly good container.
#
# The odoo-mcp reachability check is deliberately absent: MCP is not callable
# from bash. Check it from the session.
#
# Last stdout line: {"ok","context","failures":[],"warnings":[],
#                    "ram_available_gb","repos_dir"}
# Exit codes: 0 ok (or --soft) | 1 at least one check failed
set -uo pipefail

SOFT=0
[ "${1:-}" = "--soft" ] && SOFT=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/_common.sh"

MIN_REPOS="${PREFLIGHT_MIN_REPOS:-5}"
MIN_RAM_GB="${PREFLIGHT_MIN_RAM_GB:-8}"

failures=(); warnings=()

check() {
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "PASS $name" >&2
  else
    echo "FAIL $name" >&2
    failures+=("$name")
  fi
}

if command -v docker >/dev/null 2>&1; then
  context="host"
else
  context="devcontainer"
fi

repos_dir=""
if [ "$context" = host ]; then
  # An unresolvable tree becomes a FAIL on the repos check, never a crash here.
  repos_dir="${REPOS_DIR:-$("$REPO_MAP_SCRIPTS/repos-dir.sh" --raw 2>/dev/null || true)}"
  repos_populated() {
    [ -n "$repos_dir" ] && [ -d "$repos_dir" ] \
      && [ "$(ls "$repos_dir" 2>/dev/null | wc -l)" -ge "$MIN_REPOS" ]
  }
  check docker docker info --format '{{.ServerVersion}}'
  check repos repos_populated
  check devcontainer-cli devcontainer --version
else
  check odoo odoo --version
  check postgres pg_isready
fi

# Needed in both contexts: every skill downstream talks to GitHub.
check gh-auth gh auth status

# The mutable state dir lives outside the plugin tree, so a fresh machine or a
# skipped bootstrap leaves it missing. Named here rather than discovered later as
# a bogus "project unmapped" from repo-map.sh reading a file that is not there.
STATE_DIR="${ODOO_DEV_STATE_DIR:-$HOME/.local/share/odoo-dev}"
state_dir_ready() { [ -f "$STATE_DIR/repo-map.json" ]; }
check state-dir state_dir_ready

# Feature-managed skills reinstalled loose by a container rebuild shadow their
# bundled twins. Reports only; the fix belongs in the devcontainer-features repo.
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$SCRIPT_DIR/../../.." && pwd)}"
stray_skills_clean() {
  [ -z "$(bash "$PLUGIN_ROOT/scripts/check-stray-skills.sh" 2>/dev/null)" ]
}
check stray-skills stray_skills_clean

ram_gb="$(free -g 2>/dev/null | awk '/^Mem:/{print $7}')"
ram_gb="${ram_gb:-0}"
if [ "$context" = host ] && [ "$ram_gb" -lt "$MIN_RAM_GB" ]; then
  # A warning, not a failure: stack-ensure.sh evicts under pressure rather than
  # refusing, so low RAM makes things slow rather than impossible.
  warnings+=("ram: ${ram_gb} GB available, advise >= ${MIN_RAM_GB}")
  echo "WARN ${warnings[0]}" >&2
fi

ok=true
[ "${#failures[@]}" -gt 0 ] && ok=false

as_json_array() {
  [ "$#" -eq 0 ] && { echo '[]'; return; }
  printf '%s\n' "$@" | node -e '
    const lines = require("fs").readFileSync(0, "utf8").trim().split("\n");
    console.log(JSON.stringify(lines));'
}

node -e '
  const [ok, context, failures, warnings, ram, repos] = process.argv.slice(1);
  console.log(JSON.stringify({
    ok: ok === "true", context,
    failures: JSON.parse(failures), warnings: JSON.parse(warnings),
    ram_available_gb: Number(ram), repos_dir: repos || null,
  }));
' "$ok" "$context" "$(as_json_array "${failures[@]+"${failures[@]}"}")" \
  "$(as_json_array "${warnings[@]+"${warnings[@]}"}")" "$ram_gb" "$repos_dir"

if [ "$ok" = false ] && [ "$SOFT" -eq 0 ]; then
  exit 1
fi
exit 0
