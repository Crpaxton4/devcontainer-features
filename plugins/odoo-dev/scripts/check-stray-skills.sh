#!/usr/bin/env bash
# check-stray-skills.sh — detect leftover loose copies of feature-seeded skills.
#
# Six skills used to be shipped loose by the devcontainer feature, which copied
# them into the personal skills dir on every container create. The feature
# stopped shipping them (#701–#708, #738): five moved into this plugin, one was
# retired outright (#700). Copies written by pre-migration containers persist in
# the bind-mounted ~/.claude/skills, where the personal-skill scan still loads
# them — the five ALONGSIDE their odoo-dev twins (two near-identical
# descriptions competing for the same triggers, invisible until someone diffs
# them), the sixth as a playbook nobody maintains any more.
#
# REPORTS, NEVER DELETES — but unlike the pre-migration days, deleting the
# strays is now safe and IS the fix: nothing recreates them on rebuild.
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

# The names the retired sync-claude-skills publisher used to seed. Two groups,
# because they need different advice — but ONE set, and that set must match the
# list the devcontainer feature deletes in
# devcontainer-features/src/personal-features/install.sh (inside the
# sync-claude-mcp heredoc). Divergence between the two is not cosmetic: either
# the feature silently deletes a name this report never mentions, or this report
# names one that nothing removes and the report never goes quiet. Until #778
# this list was missing client-status-report while install.sh deleted it, which
# is exactly that failure. .github/scripts/test_stray_skill_parity.py now gates
# the two lists against each other, so they can no longer drift apart unnoticed.
#
# Both groups are literal lists rather than derived from the provenance banner,
# so a stray copy whose banner was edited away is still caught.

# Group 1 — moved into this plugin (#695–#699, #701–#708) and still shipped by
# it, so a loose copy SHADOWS a live twin. These five mirror
# odoo_sdk.skills.PACKAGED_SKILL_NAMES, the sources the plugin copies are
# generated from, and are the same five check-skill-parity.sh checks.
PLUGIN_SHADOWED=(
  discovery-notes
  fibonacci-estimate
  odoo-code-review
  odoo-design-doc
  odoo-quote
)

# Group 2 — retired outright (#700). No plugin twin, no packaged source, no
# replacement: it shadows nothing. It is still feature-seeded debris that loads
# as a personal skill and still spends description budget on every turn, so it
# is still a stray and the feature still deletes it — it just gets different
# advice, since there is no plugin copy to fall back on.
RETIRED_NO_TWIN=(
  client-status-report
)

# NOT managed by either list, deliberately: ingest, lint, llm-wiki-workspace,
# process and query. They sit beside the strays in <config>/skills/ on the
# machine that reported #778, but this feature never seeded them and no plugin
# ships them — they are the user's own personal skills (odoo-dev-map/SKILL.md
# records ingest, process, query and lint as Second Brain skills). Reporting
# them would be a false positive and deleting them would be data loss, so they
# are out of scope for this report and for the feature's cleanup alike. The
# parity gate asserts neither list ever acquires one of them.

strays=()
retired=()
for name in "${PLUGIN_SHADOWED[@]}"; do
  [ -f "$SKILLS_DIR/$name/SKILL.md" ] && strays+=("$name")
done
for name in "${RETIRED_NO_TWIN[@]}"; do
  [ -f "$SKILLS_DIR/$name/SKILL.md" ] && { strays+=("$name"); retired+=("$name"); }
done

if [ "$JSON" -eq 1 ]; then
  # `strays` stays the flat list every existing consumer reads; `retired` is the
  # subset with no plugin twin, so a caller can tell the two remedies apart
  # without re-deriving the grouping.
  printf '%s\n' "${strays[@]+"${strays[@]}"}" | node -e '
    const raw = require("fs").readFileSync(0, "utf8").trim();
    const strays = raw ? raw.split("\n") : [];
    const retired = process.argv[2] ? process.argv[2].split(" ") : [];
    console.log(JSON.stringify({ ok: strays.length === 0, strays,
      retired: retired.filter((n) => strays.includes(n)),
      skills_dir: process.argv[1] }));
  ' "$SKILLS_DIR" "${retired[*]-}"
  exit 0
fi

[ "${#strays[@]}" -eq 0 ] && exit 0

for name in "${strays[@]}"; do
  case " ${RETIRED_NO_TWIN[*]} " in
    *" $name "*)
      echo "stray: $SKILLS_DIR/$name/SKILL.md is a retired skill (#700) with no odoo-dev twin" ;;
    *)
      echo "stray: $SKILLS_DIR/$name/SKILL.md shadows odoo-dev:$name" ;;
  esac
done

# Name only what was actually found, so the suggested command never tells you to
# delete a directory that is not there. Brace expansion needs two or more
# alternatives; with a single stray, print the plain path.
if [ "${#strays[@]}" -gt 1 ]; then
  rm_target="{$(IFS=,; printf '%s' "${strays[*]}")}"
else
  rm_target="${strays[0]}"
fi

{
  echo
  echo "These are leftover loose copies seeded by pre-migration containers. The"
  echo "devcontainer feature ships none of them any more (#701-#708, #738) and"
  echo "nothing recreates them on rebuild, so deleting them is the fix:"
  echo
  echo "  rm -rf \"\$CLAUDE_CONFIG_DIR\"/skills/$rm_target"
  echo
  echo "The five that moved into the plugin load as personal skills alongside"
  echo "their odoo-dev twins and compete for the same triggers; those twins stay"
  echo "installed via -"
  echo
  echo "  claude plugin marketplace add Crpaxton4/devcontainer-features"
  echo "  claude plugin install odoo-dev@devcontainer-features"
  if [ "${#retired[@]}" -gt 0 ]; then
    echo
    echo "Retired with no replacement (delete only, nothing to fall back on):"
    printf '  %s\n' "${retired[@]}"
  fi
} >&2
exit 0
