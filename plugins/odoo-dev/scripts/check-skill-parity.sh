#!/usr/bin/env bash
# check-skill-parity.sh — generated skill copies must match the SDK sources.
#
# Five skills (discovery-notes, fibonacci-estimate, odoo-code-review,
# odoo-design-doc, odoo-quote) are committed generated copies of the packaged
# sources in libraries/odoo_sdk/src/odoo_sdk/skills/. A hand edit to a copy,
# or an SDK edit that was never regenerated, silently forks the skill body —
# two near-identical texts drifting apart with nobody choosing either. This
# gate regenerates the packaged set into a scratch directory and diffs each
# regenerated skill against its committed copy, byte for byte.
#
# REGENERATION, in resolution order:
#   1. odoo-sdk sync-skills --dest <scratch>   — the real installed CLI; what
#      the regeneration workflow itself runs, so CI exercises the true path.
#   2. python3 -m odoo_sdk.cli sync-skills --dest <scratch> — same code, for
#      an install without the console script on PATH.
#   3. Direct copy from <repo>/libraries/odoo_sdk/src/odoo_sdk/skills/ — the
#      --dest mode is *defined* (see odoo_sdk/cli/sync_skills.py) as a
#      byte-for-byte copy of exactly that package data, so for a repo checkout
#      the copy is equivalent. This keeps the gate runnable offline with no
#      SDK install, which is how the fixture test suite runs it.
# None available is exit 2 (the gate could not run), never a silent pass.
#
# The five names are also asserted present in the regenerated set, so a
# packaged skill that stops being generated fails here rather than passing
# vacuously; a sixth packaged skill added upstream is diffed automatically
# because the regenerated set drives the loop.
#
# Usage: check-skill-parity.sh [--skills-dir <path>] [--sdk-src <path>]
#   --skills-dir  committed copies to check (default: <plugin>/skills)
#   --sdk-src     packaged skill sources for the offline copy fallback
#                 (default: <repo>/libraries/odoo_sdk/src/odoo_sdk/skills)
# Exit codes: 0 every regenerated skill is byte-identical to its committed copy
#           | 1 at least one differs (or a committed copy is missing)
#           | 2 usage error, or no regeneration source available
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$HERE/.." && pwd)"
SKILLS_DIR="$PLUGIN_ROOT/skills"
SDK_SRC=""

while [ $# -gt 0 ]; do
  case "$1" in
    --skills-dir) SKILLS_DIR="${2:?--skills-dir needs a path}"; shift 2 ;;
    --sdk-src)    SDK_SRC="${2:?--sdk-src needs a path}"; shift 2 ;;
    *)
      echo "usage: check-skill-parity.sh [--skills-dir <path>] [--sdk-src <path>]" >&2
      exit 2
      ;;
  esac
done

if [ -z "$SDK_SRC" ]; then
  # <plugin>/.. /.. is the repo root when the plugin sits at plugins/odoo-dev/.
  SDK_SRC="$(cd "$PLUGIN_ROOT/../.." 2>/dev/null && pwd)/libraries/odoo_sdk/src/odoo_sdk/skills"
fi

# Same literal list as check-stray-skills.sh and the SDK's
# PACKAGED_SKILL_NAMES: explicit on purpose, so a regeneration that quietly
# drops one of the five fails instead of shrinking the checked set.
PACKAGED=(
  discovery-notes
  fibonacci-estimate
  odoo-code-review
  odoo-design-doc
  odoo-quote
)

# Scratch under RUNNER_TEMP in CI so the regenerated tree lands where the
# workflow expects temp files; plain mktemp everywhere else.
scratch="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/skill-parity.XXXXXX")"
trap 'rm -rf "$scratch"' EXIT
REGEN="$scratch/skills"

if command -v odoo-sdk >/dev/null 2>&1; then
  regen_via="odoo-sdk sync-skills --dest"
  odoo-sdk sync-skills --dest "$REGEN" >/dev/null
elif python3 -c 'import odoo_sdk' >/dev/null 2>&1; then
  regen_via="python3 -m odoo_sdk.cli sync-skills --dest"
  python3 -m odoo_sdk.cli sync-skills --dest "$REGEN" >/dev/null
elif [ -d "$SDK_SRC" ]; then
  regen_via="direct copy from $SDK_SRC"
  mkdir -p "$REGEN"
  for name in "${PACKAGED[@]}"; do
    # A missing source dir is reported by the presence check below, not here.
    if [ -d "$SDK_SRC/$name" ]; then cp -R "$SDK_SRC/$name" "$REGEN/$name"; fi
  done
else
  cat >&2 <<MSG
skill parity: cannot regenerate the packaged skills — odoo-sdk is not on
PATH, odoo_sdk is not importable, and no packaged sources at:

  $SDK_SRC

Install the SDK (python3 -m pip install ./libraries/odoo_sdk) or pass
--sdk-src <path>.
MSG
  exit 2
fi

for name in "${PACKAGED[@]}"; do
  if [ ! -d "$REGEN/$name" ]; then
    echo "skill parity: regeneration ($regen_via) produced no '$name' — the packaged set shrank" >&2
    exit 1
  fi
done

fails=0
for dir in "$REGEN"/*/; do
  name="$(basename "$dir")"
  committed="$SKILLS_DIR/$name"
  if [ ! -d "$committed" ]; then
    echo "skill parity: FAIL $name — no committed copy at $committed"
    fails=$((fails + 1))
    continue
  fi
  if diff_out="$(diff -r "$dir" "$committed" 2>&1)"; then
    echo "skill parity: ok   $name"
  else
    echo "skill parity: FAIL $name — committed copy differs from the packaged source:"
    printf '%s\n' "$diff_out" | sed 's/^/  /'
    fails=$((fails + 1))
  fi
done

if [ "$fails" -gt 0 ]; then
  cat >&2 <<'MSG'

These skills are generated copies. Never hand-edit them: edit the packaged
sources under libraries/odoo_sdk/src/odoo_sdk/skills/ and regenerate the
committed copies with

  odoo-sdk sync-skills --dest plugins/odoo-dev/skills

then commit the result.
MSG
  exit 1
fi

echo "skill parity: all packaged skills byte-identical to their committed copies (via $regen_via)"
