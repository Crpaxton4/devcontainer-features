#!/usr/bin/env bash
# bootstrap-state.sh — create the mutable state dir the plugin writes to.
#
# State lives OUTSIDE the plugin tree on purpose. ${CLAUDE_PLUGIN_DATA} was the
# obvious candidate and is wrong: it substitutes only in content the model
# receives, so repo-map.sh run from a terminal would resolve it to nothing and
# silently fall back to an in-tree default. A real path that both the model and a
# bare shell resolve is the honest mechanism.
#
# Idempotent. Seeds only what is absent, from in-tree .seed skeletons, and never
# touches an existing file — running this on a populated machine is a no-op.
#
# Usage: bootstrap-state.sh [--dir <path>]
# Last stdout line: {"state_dir","created":[],"existing":[]}
# Exit codes: 0 ok | 2 usage
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$HERE/.." && pwd)"
STATE="${ODOO_DEV_STATE_DIR:-$HOME/.local/share/odoo-dev}"

while [ $# -gt 0 ]; do
  case "$1" in
    --dir) STATE="${2:?--dir needs a path}"; shift 2 ;;
    *) echo "usage: bootstrap-state.sh [--dir <path>]" >&2; exit 2 ;;
  esac
done

created=(); existing=()

seed() { # seed <target-relative-path> <seed-file>
  local target="$STATE/$1" src="$2"
  if [ -e "$target" ]; then existing+=("$1"); return; fi
  mkdir -p "$(dirname "$target")"
  cp "$src" "$target"
  created+=("$1")
}

mkdir_once() {
  if [ -d "$STATE/$1" ]; then existing+=("$1/"); else mkdir -p "$STATE/$1"; created+=("$1/"); fi
}

mkdir -p "$STATE"
mkdir_once tasks
mkdir_once upgrade-lessons

seed repo-map.json "$PLUGIN_ROOT/skills/odoo-repo-map/repo-map.json.seed"
seed upgrade-lessons/README.md "$PLUGIN_ROOT/skills/odoo-upgrade/upgrade-lessons-README.md.seed"

as_json_array() {
  [ "$#" -eq 0 ] && { echo '[]'; return; }
  printf '%s\n' "$@" | node -e '
    const lines = require("fs").readFileSync(0, "utf8").trim().split("\n");
    console.log(JSON.stringify(lines));'
}

node -e '
  const [state, created, existing] = process.argv.slice(1);
  console.log(JSON.stringify({ state_dir: state,
    created: JSON.parse(created), existing: JSON.parse(existing) }));
' "$STATE" "$(as_json_array "${created[@]+"${created[@]}"}")" \
  "$(as_json_array "${existing[@]+"${existing[@]}"}")"
