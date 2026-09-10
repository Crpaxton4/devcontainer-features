#!/usr/bin/env bash
# check-tool-contract.sh — every tool name the plugin references must exist.
#
# The plugin's skills, agents, commands, and hooks talk about the odoo-mcp/
# odoo-sdk command surface by name. A rename or removal in the SDK does not
# break anything loudly here — the text keeps naming a tool that no longer
# exists, and an agent following it discovers that mid-task. This gate makes
# the reference itself the contract: every name the plugin tree mentions must
# be on the surface `odoo-sdk cmd --list --json` actually serves.
#
# WHAT COUNTS AS A REFERENCE (the matching decision, kept conservative):
#   1. `mcp__odoo-mcp__<name>`   — a fully qualified MCP tool id.
#   2. `odoo-sdk cmd <name>`     — a CLI dispatch of a registry command
#                                  (flags like --list start with '-' and
#                                  never match the [a-z_]+ name).
#   3. Bare names ONLY in the two forms the plugin already uses when it names
#      a tool without the mcp__ prefix:
#         odoo-mcp `<name>`      (prefix form,  e.g. odoo-mcp `start_task`)
#         `<name>` tool          (suffix form,  e.g. the `start_task` tool)
#      Generic backticked snake_case is deliberately NOT matched: the tree is
#      full of field names, JSON keys, and gate vocabulary (`default_branch`,
#      `view_mode`, `tours_skipped`, ...) that would drown the gate in false
#      positives. A bare mention outside these two forms is invisible to this
#      gate — acceptable, because a missed reference costs nothing while a
#      false positive blocks every pull request.
#
# THE SURFACE, in resolution order:
#   1. --surface-file <path>     — one name per line ('#' comments allowed);
#                                  how the offline fixture tests inject a
#                                  known surface.
#   2. odoo-sdk cmd --list --json — the real installed CLI.
#   3. python3 -c "from odoo_sdk.commands.builtin import BUILTIN_COMMANDS"
#                                — the exact mapping register_builtins() puts
#                                  on the registry `cmd --list` enumerates,
#                                  for an install without the console script.
# None available is exit 2 (the gate could not run), never a silent pass.
#
# Scanned: skills/ agents/ commands/ hooks/ scripts/ under the plugin root.
# Excluded: scripts/tests/ (fixtures reference unknown names on purpose) and
# this script itself (its own documentation names the patterns).
#
# Usage: check-tool-contract.sh [--plugin-root <path>] [--surface-file <path>]
# Exit codes: 0 every referenced name is on the surface
#           | 1 at least one referenced name is not
#           | 2 usage error, or no surface source available
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$HERE/.." && pwd)"
SURFACE_FILE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --plugin-root)  PLUGIN_ROOT="${2:?--plugin-root needs a path}"; shift 2 ;;
    --surface-file) SURFACE_FILE="${2:?--surface-file needs a path}"; shift 2 ;;
    *)
      echo "usage: check-tool-contract.sh [--plugin-root <path>] [--surface-file <path>]" >&2
      exit 2
      ;;
  esac
done

# --- referenced names, as "name<TAB>file:line" ------------------------------------

# Every scannable file: the five component trees, minus the test fixtures and
# this script. find over grep -r so the exclusions are explicit paths, not
# --exclude-dir name matching that could swallow a future skills/*/tests.
scan_files() {
  local dir
  for dir in skills agents commands hooks scripts; do
    [ -d "$PLUGIN_ROOT/$dir" ] || continue
    find "$PLUGIN_ROOT/$dir" -type f \
      ! -path "$PLUGIN_ROOT/scripts/tests/*" \
      ! -name "$(basename "${BASH_SOURCE[0]}")"
  done
}

# extract <ERE> <sed-extract> — print "name<TAB>file:line" for every match of
# the reference pattern; sed strips the pattern down to the bare tool name.
extract() {
  local pattern="$1" strip="$2"
  scan_files | while IFS= read -r f; do
    # grep exits 1 on no match; that is the normal case for most files.
    grep -noE "$pattern" "$f" 2>/dev/null | while IFS=: read -r line match; do
      printf '%s\t%s:%s\n' "$(printf '%s' "$match" | sed -E "$strip")" \
        "${f#"$PLUGIN_ROOT"/}" "$line"
    done || true
  done
}

REFS="$(
  {
    extract 'mcp__odoo-mcp__[a-z_]+'      's/^mcp__odoo-mcp__//'
    extract 'odoo-sdk cmd [a-z_]+'        's/^odoo-sdk cmd //'
    extract 'odoo-mcp `[a-z_]+`'          's/^odoo-mcp `//; s/`$//'
    extract '`[a-z_]+` tool'              's/^`//; s/` tool$//'
  } | LC_ALL=C sort -u
)"

if [ -z "$REFS" ]; then
  echo "tool contract: no tool references found under ${PLUGIN_ROOT} — nothing to check"
  exit 0
fi

# --- the actual surface -----------------------------------------------------------

if [ -n "$SURFACE_FILE" ]; then
  [ -f "$SURFACE_FILE" ] || { echo "tool contract: surface file not found: $SURFACE_FILE" >&2; exit 2; }
  SURFACE="$(grep -vE '^[[:space:]]*(#|$)' "$SURFACE_FILE" | LC_ALL=C sort -u)"
elif command -v odoo-sdk >/dev/null 2>&1; then
  SURFACE="$(odoo-sdk cmd --list --json | python3 -c '
import json, sys
for entry in json.load(sys.stdin):
    print(entry["name"])
' | LC_ALL=C sort -u)"
elif python3 -c 'import odoo_sdk' >/dev/null 2>&1; then
  SURFACE="$(python3 -c '
from odoo_sdk.commands.builtin import BUILTIN_COMMANDS
print("\n".join(sorted(BUILTIN_COMMANDS)))
')"
else
  cat >&2 <<'MSG'
tool contract: cannot resolve the command surface — odoo-sdk is not on PATH
and odoo_sdk is not importable. Install the SDK first:

  python3 -m pip install ./libraries/odoo_sdk

or pass an explicit listing with --surface-file <path>.
MSG
  exit 2
fi

if [ -z "$SURFACE" ]; then
  echo "tool contract: the resolved surface is empty — refusing to pass on it" >&2
  exit 2
fi

# --- referenced ⊆ surface ---------------------------------------------------------

names="$(printf '%s\n' "$REFS" | cut -f1 | LC_ALL=C sort -u)"
unknown="$(LC_ALL=C comm -23 <(printf '%s\n' "$names") <(printf '%s\n' "$SURFACE"))"

if [ -n "$unknown" ]; then
  echo "tool contract: the plugin references tools that are not on the surface:"
  while IFS= read -r name; do
    printf '%s\n' "$REFS" | awk -F'\t' -v n="$name" '$1 == n { print "  " n "  (" $2 ")" }'
  done <<< "$unknown"
  cat >&2 <<'MSG'

Every name above is referenced by a plugin file but absent from
`odoo-sdk cmd --list --json`. Either the tool was renamed/removed in the SDK
(fix the plugin text to the current name) or the reference has a typo.
MSG
  exit 1
fi

total_refs="$(printf '%s\n' "$REFS" | wc -l | tr -d ' ')"
total_names="$(printf '%s\n' "$names" | wc -l | tr -d ' ')"
surface_count="$(printf '%s\n' "$SURFACE" | wc -l | tr -d ' ')"
echo "tool contract: ${total_refs} references to ${total_names} distinct tool(s), all on the ${surface_count}-command surface"
