#!/usr/bin/env bash
# check-stray-skills.test.sh — offline tests for check-stray-skills.sh.
#
# No network, no docker, no SDK: every case runs against a throwaway skills dir
# built by hand under --skills-dir, so the real ~/.claude/skills is never read
# and never touched.
#
# What matters here is the NAME LIST, not the exit code — the script is exit 0
# always by design ("this is a report, not a gate") and validate.sh gates on its
# stdout instead. So every case asserts on what was and was not printed.
#
# #778: the reported set used to be the five plugin-shadowed names while the
# devcontainer feature's cleanup deleted six, so client-status-report was
# removed by a script that never mentioned it. Both halves are asserted below;
# .github/scripts/test_stray_skill_parity.py keeps the two lists equal.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$HERE/../check-stray-skills.sh"
work="$(mktemp -d "${TMPDIR:-/tmp}/stray-skills-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
ok()  { echo "PASS $*"; pass=$((pass + 1)); }
bad() { echo "FAIL $*"; fail=$((fail + 1)); }

seed() {  # seed <dir> <name>...  — a directory carrying a SKILL.md
  local dir="$1"; shift
  local name
  for name in "$@"; do
    mkdir -p "$dir/$name"
    printf 'seeded %s\n' "$name" > "$dir/$name/SKILL.md"
  done
}

# --- a clean skills dir is silent ----------------------------------------------
# preflight.sh and validate.sh both test `[ -z "$(check-stray-skills.sh)" ]`, so
# a single stray byte of chatter on a clean machine is a false failure there.

clean="$work/clean"
seed "$clean" my-own-skill ingest lint llm-wiki-workspace process query
out="$(bash "$SUT" --skills-dir "$clean" 2>/dev/null)"; rc=$?
if [ -z "$out" ] && [ "$rc" -eq 0 ]; then
  ok "clean: silent on stdout and exits 0"
else
  bad "clean: rc=$rc, stdout: $out"
fi

# The five personal Second Brain skills above are the USER's, not the feature's.
# Naming one here would be a false positive, and the feature's cleanup takes its
# list from the same set — so it would also be a deletion.
if bash "$SUT" --skills-dir "$clean" 2>&1 \
   | grep -Eq 'ingest|^stray.*\blint\b|llm-wiki-workspace|process|query'; then
  bad "clean: reported a user-owned personal skill"
else
  ok "clean: user-owned personal skills are never reported"
fi

# --- every feature-seeded name is reported -------------------------------------

dirty="$work/dirty"
seed "$dirty" discovery-notes fibonacci-estimate odoo-code-review \
  odoo-design-doc odoo-quote client-status-report my-own-skill lint
out="$(bash "$SUT" --skills-dir "$dirty" 2>/dev/null)"
missing=""
for name in discovery-notes fibonacci-estimate odoo-code-review \
            odoo-design-doc odoo-quote client-status-report; do
  printf '%s' "$out" | grep -q "/$name/SKILL.md" || missing="$missing $name"
done
if [ -z "$missing" ]; then
  ok "dirty: all six feature-seeded names are reported"
else
  bad "dirty: not reported:$missing"
fi

# The #778 name specifically, and with the advice that fits it: there is no
# odoo-dev:client-status-report to fall back on.
if printf '%s' "$out" | grep -q "client-status-report/SKILL.md is a retired skill"; then
  ok "dirty: client-status-report is reported as retired, not as shadowing"
else
  bad "dirty: client-status-report not reported as retired: $out"
fi
if printf '%s' "$out" | grep -q "odoo-quote/SKILL.md shadows odoo-dev:odoo-quote"; then
  ok "dirty: a plugin-shadowed name names its twin"
else
  bad "dirty: odoo-quote did not name its twin: $out"
fi

if printf '%s' "$out" | grep -Eq '\bmy-own-skill\b|/lint/'; then
  bad "dirty: reported a skill the feature does not own: $out"
else
  ok "dirty: a user-authored skill beside the strays is left out of the report"
fi

# --- the suggested command names only what was found ---------------------------
# A copy-pasteable `rm -rf` that lists directories which are not there teaches
# the reader to ignore it.

one="$work/one"
seed "$one" odoo-quote
advice="$(bash "$SUT" --skills-dir "$one" 2>&1 >/dev/null)"
if printf '%s' "$advice" | grep -qF 'rm -rf "$CLAUDE_CONFIG_DIR"/skills/odoo-quote'; then
  ok "one stray: suggests a plain path, not a one-element brace expansion"
else
  bad "one stray: bad rm suggestion: $advice"
fi
if printf '%s' "$advice" | grep -q 'fibonacci-estimate'; then
  bad "one stray: suggested deleting a directory that is not there: $advice"
else
  ok "one stray: suggests deleting nothing that is absent"
fi

advice="$(bash "$SUT" --skills-dir "$dirty" 2>&1 >/dev/null)"
if printf '%s' "$advice" | grep -qF 'skills/{discovery-notes,'; then
  ok "many strays: suggests a brace expansion over the found set"
else
  bad "many strays: bad rm suggestion: $advice"
fi

# --- --json stays machine-readable ---------------------------------------------

json="$(bash "$SUT" --skills-dir "$clean" --json 2>/dev/null)"
if printf '%s' "$json" | grep -qF '"ok":true' \
   && printf '%s' "$json" | grep -qF '"strays":[]'; then
  ok "json: clean dir reports ok with an empty stray list"
else
  bad "json: clean dir: $json"
fi

json="$(bash "$SUT" --skills-dir "$dirty" --json 2>/dev/null)"
if printf '%s' "$json" | grep -qF '"ok":false' \
   && printf '%s' "$json" | grep -qF '"retired":["client-status-report"]' \
   && printf '%s' "$json" | grep -qF '"odoo-quote"'; then
  ok "json: dirty dir separates the retired name from the shadowed ones"
else
  bad "json: dirty dir: $json"
fi

# --- a directory with no SKILL.md is not a skill --------------------------------
# The same definition of "stray" the feature's cleanup uses, so the two agree on
# what counts as well as on which names count.

nomd="$work/no-skill-md"
mkdir -p "$nomd/odoo-quote"
printf 'loose notes\n' > "$nomd/odoo-quote/notes.txt"
out="$(bash "$SUT" --skills-dir "$nomd" 2>/dev/null)"
if [ -z "$out" ]; then
  ok "no SKILL.md: a same-named directory without one is not reported"
else
  bad "no SKILL.md: unexpectedly reported: $out"
fi

# --- a missing skills dir must not explode --------------------------------------
# A container that never created ~/.claude/skills is clean, not broken.

out="$(bash "$SUT" --skills-dir "$work/does-not-exist" 2>/dev/null)"; rc=$?
if [ -z "$out" ] && [ "$rc" -eq 0 ]; then
  ok "absent skills dir: silent and exits 0"
else
  bad "absent skills dir: rc=$rc, stdout: $out"
fi

echo
echo "check-stray-skills.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
