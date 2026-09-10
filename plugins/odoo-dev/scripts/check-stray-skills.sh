#!/usr/bin/env bash
# check-stray-skills.sh — detect feature-managed skills reinstalled loose.
#
# Five of the bundled skills are also shipped by a devcontainer feature, which
# writes them one level deep under the personal skills dir on every container
# create. That location is exactly what the personal-skill scan matches, so after
# a rebuild they load ALONGSIDE their odoo-dev twins: two near-identical
# descriptions competing for the same triggers, invisible until someone diffs them.
#
# REPORTS, NEVER DELETES. Deleting a file the feature will recreate on the next
# rebuild is a loop; the fix belongs in the devcontainer-features repo, which must
# stop shipping loose skills and let the marketplace install carry them.
#
# Silent and exit 0 when clean, so `[ -z "$(check-stray-skills.sh)" ]` is a valid
# test and preflight.sh can call it as a check.
#
# Usage: check-stray-skills.sh [--skills-dir <path>] [--json]
# Exit codes: 0 always (this is a report, not a gate)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The subject is always the personal skills dir, <config>/skills/, wherever this
# script happens to be run from. It used to be derived as the parent of the plugin
# root, which was right only while the plugin WAS a checkout sitting inside that dir.
# Installed from the marketplace, the plugin root is
# <config>/plugins/cache/devcontainer-features/odoo-dev/<version>/ and its parent is not a skills
# dir at all — so the check reported clean no matter how many strays were loaded,
# which is worse than not running it. Resolve <config> the way the CLI does.
CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SKILLS_DIR="$CONFIG_DIR/skills"
JSON=0

while [ $# -gt 0 ]; do
  case "$1" in
    --skills-dir) SKILLS_DIR="${2:?--skills-dir needs a path}"; shift 2 ;;
    --json) JSON=1; shift ;;
    *) echo "usage: check-stray-skills.sh [--skills-dir <path>] [--json]" >&2; exit 0 ;;
  esac
done

# The five the devcontainer feature owns. Kept as a literal list rather than
# derived from the banner, so a stray copy whose banner was edited away is still
# caught.
FEATURE_MANAGED=(
  discovery-notes
  fibonacci-estimate
  odoo-code-review
  odoo-design-doc
  odoo-quote
)

strays=()
for name in "${FEATURE_MANAGED[@]}"; do
  [ -f "$SKILLS_DIR/$name/SKILL.md" ] && strays+=("$name")
done

if [ "$JSON" -eq 1 ]; then
  printf '%s\n' "${strays[@]+"${strays[@]}"}" | node -e '
    const raw = require("fs").readFileSync(0, "utf8").trim();
    const strays = raw ? raw.split("\n") : [];
    console.log(JSON.stringify({ ok: strays.length === 0, strays,
      skills_dir: process.argv[1] }));
  ' "$SKILLS_DIR"
  exit 0
fi

[ "${#strays[@]}" -eq 0 ] && exit 0

for name in "${strays[@]}"; do
  echo "stray: $SKILLS_DIR/$name/SKILL.md shadows odoo-dev:$name"
done
cat >&2 <<'MSG'

These are feature-managed skills reinstalled loose by a container rebuild. They
load as personal skills alongside their bundled twins and compete for the same
triggers. Do not delete them here — the next rebuild recreates them. Fix it in
the devcontainer-features repo (src/personal-features/skills/): stop shipping
loose skills, and let the odoo-dev marketplace install carry them instead —

  claude plugin marketplace add Crpaxton4/devcontainer-features
  claude plugin install odoo-dev@devcontainer-features
MSG
exit 0
