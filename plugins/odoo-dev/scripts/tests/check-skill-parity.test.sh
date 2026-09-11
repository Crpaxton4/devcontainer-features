#!/usr/bin/env bash
# check-skill-parity.test.sh — offline tests for check-skill-parity.sh.
#
# No network, no docker, no Odoo, no SDK install required: without odoo-sdk on
# PATH the gate regenerates via its documented direct-copy fallback from
# libraries/odoo_sdk/src/odoo_sdk/skills/ (byte-for-byte what --dest copies),
# which is present in any repo checkout. The failure cases run against a temp
# COPY of the committed skills — the real tree is never touched.
#
# Failures asserted by the skill they must name and by the regeneration
# command the message must carry, not by exit code alone.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$HERE/../check-skill-parity.sh"
PLUGIN_ROOT="$(cd "$HERE/../.." && pwd)"
work="$(mktemp -d "${TMPDIR:-/tmp}/skill-parity-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
ok()  { echo "PASS $*"; pass=$((pass + 1)); }
bad() { echo "FAIL $*"; fail=$((fail + 1)); }

# --- the real tree must be in parity --------------------------------------------

out="$(bash "$SUT" 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q "byte-identical"; then
  ok "real tree: committed copies match the packaged sources"
else
  bad "real tree: rc=$rc: $out"
fi

# --- a hand-edited committed copy must fail and name itself ---------------------

cp -R "$PLUGIN_ROOT/skills" "$work/edited-skills"
printf '\nHAND EDIT THAT MUST FAIL PARITY\n' \
  >> "$work/edited-skills/discovery-notes/SKILL.md"

out="$(bash "$SUT" --skills-dir "$work/edited-skills" 2>&1)"; rc=$?
if [ "$rc" -ne 1 ]; then
  bad "hand edit: expected exit 1, got $rc: $out"
else
  ok "hand edit: exits 1"
fi
if printf '%s' "$out" | grep -q "FAIL discovery-notes"; then
  ok "hand edit: names discovery-notes"
else
  bad "hand edit: did not name discovery-notes: $out"
fi
# The other four generated skills were not edited and must still pass.
if printf '%s' "$out" | grep -q "ok   odoo-quote"; then
  ok "hand edit: untouched skills still pass"
else
  bad "hand edit: odoo-quote should still pass: $out"
fi
# The message must hand the reader the fix.
if printf '%s' "$out" | grep -qF "odoo-sdk sync-skills --dest plugins/odoo-dev/skills"; then
  ok "hand edit: message carries the regeneration command"
else
  bad "hand edit: no regeneration command in: $out"
fi

# --- a deleted committed copy must fail, not vanish from the check --------------

cp -R "$PLUGIN_ROOT/skills" "$work/missing-skills"
rm -rf "$work/missing-skills/odoo-quote"

out="$(bash "$SUT" --skills-dir "$work/missing-skills" 2>&1)"; rc=$?
if [ "$rc" -eq 1 ] && printf '%s' "$out" | grep -q "no committed copy.*odoo-quote"; then
  ok "missing copy: exits 1 and names odoo-quote"
else
  bad "missing copy: expected exit 1 naming odoo-quote, got rc=$rc: $out"
fi

# --- no regeneration source must refuse to run (exit 2), never pass -------------

# Only meaningful where the copy fallback is the active path; with a real SDK
# installed the gate rightly ignores --sdk-src and regenerates via the CLI.
if ! command -v odoo-sdk >/dev/null 2>&1 \
   && ! python3 -c 'import odoo_sdk' >/dev/null 2>&1; then
  out="$(bash "$SUT" --sdk-src "$work/no-such-src" 2>&1)"; rc=$?
  if [ "$rc" -eq 2 ]; then
    ok "no regeneration source: exits 2, never a silent pass"
  else
    bad "no regeneration source: expected exit 2, got $rc: $out"
  fi
else
  echo "SKIP no-regeneration-source case (an installed SDK takes precedence)"
fi

echo
echo "check-skill-parity.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
