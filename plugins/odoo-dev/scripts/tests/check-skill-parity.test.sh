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
#
# Because the suite runs without a working odoo-sdk, the CLI tier would never
# be exercised at all — which is how #775 survived. So two cases put a stub
# `odoo-sdk` early on PATH (one that does not import, one that imports but has
# no sync-skills subcommand) and assert the gate falls through to a tier that
# works instead of dying on it.
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

# --- a broken odoo-sdk on PATH must fall through, not kill the gate (#775) ------

# Git worktrees share the main checkout's .venv, so a concurrent worker can
# leave the one console script every worker resolves stale or half-rewritten.
# `command -v` still finds it; running it does not work. Under the gate's
# `set -euo pipefail` that used to abort the run outright, so tier 1 was fatal
# rather than a tier. Two stubs, one per way a console script can be broken.
stub_bin="$work/broken-bin"
mkdir -p "$stub_bin"

# (a) does not import at all — `--help` itself fails.
cat > "$stub_bin/odoo-sdk" <<'STUB'
#!/usr/bin/env bash
echo "ModuleNotFoundError: No module named 'odoo_sdk'" >&2
exit 1
STUB
chmod +x "$stub_bin/odoo-sdk"

out="$(PATH="$stub_bin:$PATH" bash "$SUT" 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q "byte-identical"; then
  ok "broken odoo-sdk (no import): falls through to a working tier"
else
  bad "broken odoo-sdk (no import): expected exit 0, got rc=$rc: $out"
fi
if printf '%s' "$out" | grep -qF "via odoo-sdk sync-skills --dest"; then
  bad "broken odoo-sdk (no import): claimed to regenerate via the broken CLI: $out"
else
  ok "broken odoo-sdk (no import): did not credit the broken CLI"
fi

# (b) imports and answers --help, but rejects sync-skills — an older SDK build
# left on PATH by a shared venv. The --help probe alone cannot see this, so
# the tier must also be judged by whether the regeneration itself worked.
cat > "$stub_bin/odoo-sdk" <<'STUB'
#!/usr/bin/env bash
if [ "${1:-}" = "--help" ]; then
  echo "usage: odoo-sdk [-h] {list,report} ..."
  exit 0
fi
echo "odoo-sdk: error: argument command: invalid choice: '${1:-}'" >&2
exit 2
STUB
chmod +x "$stub_bin/odoo-sdk"

out="$(PATH="$stub_bin:$PATH" bash "$SUT" 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q "byte-identical"; then
  ok "stale odoo-sdk (no sync-skills): falls through to a working tier"
else
  bad "stale odoo-sdk (no sync-skills): expected exit 0, got rc=$rc: $out"
fi
if printf '%s' "$out" | grep -q "falling through"; then
  ok "stale odoo-sdk (no sync-skills): says on stderr that it fell through"
else
  bad "stale odoo-sdk (no sync-skills): fell through silently: $out"
fi

# A broken tier 1 must still not become a silent pass when nothing else works.
out="$(PATH="$stub_bin:$PATH" bash "$SUT" --sdk-src "$work/no-such-src" 2>&1)"; rc=$?
if python3 -c 'import odoo_sdk' >/dev/null 2>&1; then
  echo "SKIP broken-tier-1 exhaustion case (an importable odoo_sdk takes precedence)"
elif [ "$rc" -eq 2 ]; then
  ok "broken odoo-sdk with no other source: exits 2, never a silent pass"
else
  bad "broken odoo-sdk with no other source: expected exit 2, got $rc: $out"
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
