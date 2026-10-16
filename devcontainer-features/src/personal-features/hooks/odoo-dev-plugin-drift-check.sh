#!/usr/bin/env bash
# odoo-dev-plugin-drift-check.sh - SessionStart hook: say so, loudly, when the
# installed odoo-dev plugin is not the one the marketplace clone carries.
#
# WHY THIS EXISTS (#1052): a devcontainer ran odoo-dev 1.2.0 while main was
# several majors ahead, and nothing said so. "Already installed" is satisfied by
# any cached copy, so bugs already fixed on main (#888, #961, #1039) kept
# recurring in sessions that could not tell their plugin was stale. sync-claude-mcp
# now refreshes the plugin on every container create; this hook is the check that
# still fires when that refresh failed, never ran, or ran in another container
# that shares the same bind-mounted ~/.claude.
#
# WHAT IT COMPARES, with no network, from files Claude Code already keeps:
#   * the version recorded for odoo-dev@devcontainer-features in
#     $CLAUDE_CONFIG_DIR/plugins/installed_plugins.json
#   * the version in plugins/odoo-dev/.claude-plugin/plugin.json inside the
#     marketplace clone, $CLAUDE_CONFIG_DIR/plugins/marketplaces/devcontainer-features
#   * the version directories under $CLAUDE_CONFIG_DIR/plugins/cache/
#     devcontainer-features/odoo-dev/
# It warns when the first two differ, or when more than one cache directory is
# LIVE. A directory carrying an .orphaned_at marker is not live: Claude Code
# writes that marker on the superseded version after every update and deletes
# the directory days later, so counting it would warn after every healthy update.
#
# The clone is only as fresh as the last `claude plugin marketplace update`, so
# this cannot see a marketplace that has moved on since. That needs the network,
# which a SessionStart hook must not wait for.
#
# CONTRACT - this must never disrupt a session:
#   * ALWAYS exit 0. For SessionStart, exit 2 BLOCKS the session from starting.
#   * Write to stdout ONLY the one warning line. A SessionStart hook's plain
#     stdout is added to the session context verbatim, so any other byte is
#     noise in every session. Consistent plugin -> no output at all.
#   * No network, no writes. Needs jq; without jq it stays silent.
#
# Deliberately NOT `set -e`/`set -o pipefail`: a failure anywhere in here must
# degrade to "no warning", never to a nonzero exit.
set -u

CFG="${CLAUDE_CONFIG_DIR:-${HOME:-}/.claude}"
REGISTRY="$CFG/plugins/installed_plugins.json"
MANIFEST="$CFG/plugins/marketplaces/devcontainer-features/plugins/odoo-dev/.claude-plugin/plugin.json"
CACHE="$CFG/plugins/cache/devcontainer-features/odoo-dev"

command -v jq >/dev/null 2>&1 || exit 0
[ -r "$REGISTRY" ] || exit 0

installed="$(jq -r '.plugins["odoo-dev@devcontainer-features"][0].version // empty' "$REGISTRY" 2>/dev/null || true)"
# Not installed (or an unreadable registry): there is no second version to
# disagree with, and the install step in sync-claude-mcp reports that case.
[ -n "$installed" ] || exit 0

problems=""

available=""
if [ -r "$MANIFEST" ]; then
    available="$(jq -r '.version // empty' "$MANIFEST" 2>/dev/null || true)"
fi
if [ -n "$available" ] && [ "$available" != "$installed" ]; then
    problems="installed version is $installed but the marketplace clone carries $available"
fi

live=""
live_count=0
if [ -d "$CACHE" ]; then
    for dir in "$CACHE"/*/; do
        [ -d "$dir" ] || continue
        [ -e "${dir}.orphaned_at" ] && continue
        live="${live:+$live, }$(basename "$dir")"
        live_count=$((live_count + 1))
    done
fi
if [ "$live_count" -gt 1 ]; then
    problems="${problems:+$problems; }$live_count version directories under $CACHE ($live)"
fi

[ -n "$problems" ] || exit 0

printf '%s\n' "WARNING: the odoo-dev plugin in this container is stale or inconsistent: $problems. Fixes already on main may be missing from this session. Run 'claude plugin update odoo-dev@devcontainer-features' and restart the session."
exit 0
