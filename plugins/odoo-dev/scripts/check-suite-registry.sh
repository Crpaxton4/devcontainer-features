#!/usr/bin/env bash
# check-suite-registry.sh — reconcile the plugin's two test-suite registries.
#
# There are two, and until #781 nothing kept them in agreement:
#
#   1. validate.sh's `run_suite` block — an explicit, hand-maintained list. This
#      is what a developer runs locally, and what a local "all gates passed"
#      is a statement about.
#   2. .github/workflows/plugin-odoo-dev.yaml's `script-tests` job — enumerated
#      by `find plugins/odoo-dev -name '*.test.sh' -type f`, so it runs
#      everything on disk whether or not anyone registered it.
#
# The failure this removes is not "a suite does not run" — CI's find sweep runs
# every one of them. It is that *reading either registry alone gives a confident
# wrong answer about coverage*. #781 records a reviewer concluding from
# validate.sh that a suite was "dead code in CI" and that its PR "would land
# green having never run its own tests". CI had run it and logged PASS. The gap
# had also been measured three times at three different values, because it moves
# whenever anyone adds a suite.
#
# So: disk is the source of truth, and every file on it must be either
# REGISTERED in validate.sh or NAMED below with a reason. Silence is no longer
# an option, and neither is a stale entry — an opt-out for a suite that no
# longer exists, or for one that has since been registered, fails too.
#
# NO COUNTS ANYWHERE. The defect being fixed is a number living in two places;
# a gate that asserted "there are 19 suites" would be a third copy of it, and
# would break the first time a sibling branch added one. Everything here is set
# arithmetic over paths.
#
# Usage: check-suite-registry.sh [--plugin-root <dir>] [--validate <file>]
#                                [--ci-only-file <file>] [--list]
#   --plugin-root    tree searched for *.test.sh      (default: this script's ..)
#   --validate       file the run_suite block is read from (default: <root>/scripts/validate.sh)
#   --ci-only-file   replaces the built-in opt-out list; a test seam, one
#                    "<relpath><TAB><reason>" per line, blank lines and # ignored
#   --list           print every suite on disk with its disposition, then exit 0
#
# Exit codes: 0 the two registries agree | 1 they do not | 2 cannot be checked
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$HERE/.." && pwd)"
VALIDATE=""
CI_ONLY_FILE=""
LIST=0

while [ $# -gt 0 ]; do
  case "$1" in
    --plugin-root)   PLUGIN_ROOT="${2:?--plugin-root needs a path}"; shift 2 ;;
    --validate)      VALIDATE="${2:?--validate needs a path}"; shift 2 ;;
    --ci-only-file)  CI_ONLY_FILE="${2:?--ci-only-file needs a path}"; shift 2 ;;
    --list)          LIST=1; shift ;;
    -h|--help)
      sed -n '/^# Usage:/,/^# Exit codes:/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "check-suite-registry.sh: unknown argument '$1'" >&2; exit 2 ;;
  esac
done

PLUGIN_ROOT="$(cd "$PLUGIN_ROOT" 2>/dev/null && pwd)" || {
  echo "check-suite-registry.sh: --plugin-root does not exist" >&2; exit 2; }
[ -n "$VALIDATE" ] || VALIDATE="$PLUGIN_ROOT/scripts/validate.sh"
[ -f "$VALIDATE" ] || {
  echo "check-suite-registry.sh: cannot read the registry: $VALIDATE" >&2; exit 2; }

# --- the opt-out list ------------------------------------------------------------
# Suites validate.sh deliberately does NOT run, each with the reason it does not.
# Paths are relative to the plugin root. A reason is mandatory: an unexplained
# entry here is the same silence this script exists to remove, one level down.
#
# The bar for being on this list is NOT "it runs in CI" — every suite does. It is
# that running it from validate.sh would put a PASS on the screen that does not
# mean what a PASS means. Each of these would report success while its real
# subject went unexercised.
#
# Every entry is verified in both directions: the path must exist on disk, and it
# must NOT also be registered in validate.sh. An opt-out that outlived its suite,
# or one contradicted by a registration, fails this check.
CI_ONLY=(
  "scripts/tests/check-tool-contract.test.sh	the tool-contract job pip-installs libraries/odoo_sdk first; without it the 'real tree via installed SDK' case SKIPs, which is the case that checks the surface the agents actually call"
  "scripts/tests/gh-url.test.sh	ruled in #781 to stay unregistered: CI's find-based script-tests job already runs it on every push, and registering it would collide on the run_suite block concurrent branches append to"
  "skills/odoo-upgrade/scripts/tests/studio-inventory.test.sh	needs psql; with no Postgres client it exits 0 after printing 'SKIP: psql not available', so validate.sh would print PASS for a suite that executed no case"
)

if [ -n "$CI_ONLY_FILE" ]; then
  [ -f "$CI_ONLY_FILE" ] || {
    echo "check-suite-registry.sh: --ci-only-file does not exist: $CI_ONLY_FILE" >&2; exit 2; }
  CI_ONLY=()
  while IFS= read -r line; do
    case "$line" in ""|\#*) continue ;; esac
    CI_ONLY+=("$line")
  done < "$CI_ONLY_FILE"
fi

findings=()
note() { findings+=("$1"); }

ci_only_path()   { printf '%s' "${1%%$'\t'*}"; }
ci_only_reason() { local r="${1#*$'\t'}"; [ "$r" = "$1" ] && r=""; printf '%s' "$r"; }

# --- what is on disk -------------------------------------------------------------
# The same enumeration the workflow's script-tests job uses, so "disk" means the
# same set in both places. Keep the two in step if either ever changes.
on_disk=()
while IFS= read -r f; do
  [ -n "$f" ] || continue
  on_disk+=("${f#"$PLUGIN_ROOT"/}")
done < <(find "$PLUGIN_ROOT" -name '*.test.sh' -type f | sort)

if [ "${#on_disk[@]}" -eq 0 ]; then
  # Not "clean": a find that matches nothing means the tree moved or the glob
  # broke, and reporting agreement would be the false pass all over again.
  echo "no *.test.sh found under $PLUGIN_ROOT — the enumeration is broken, not the tree empty"
  exit 1
fi

# --- what validate.sh registers --------------------------------------------------
# The second argument of each `run_suite` call, with the variables validate.sh
# resolves paths through expanded back to plugin-root-relative form. A path built
# from anything else is reported rather than guessed at: this gate is worthless if
# it silently drops a registration it could not parse.
registered=()
while IFS= read -r raw; do
  [ -n "$raw" ] || continue
  p="$raw"
  p="${p//\$\{HERE\}/\$HERE}"
  p="${p//\$\{SKILLS\}/\$SKILLS}"
  p="${p//\$\{ROOT\}/\$ROOT}"
  p="${p//\$HERE/scripts}"
  p="${p//\$SKILLS/skills}"
  p="${p//\$ROOT\//}"
  case "$p" in
    *'$'*)
      note "unparseable registration in ${VALIDATE##*/}: $raw"
      note "  this gate resolves \$HERE, \$SKILLS and \$ROOT only. A registration it cannot resolve is one it cannot reconcile, so extend the resolver rather than leaving it unreadable."
      continue ;;
  esac
  registered+=("$p")
done < <(sed -nE 's/^[[:space:]]*run_suite[[:space:]]+"[^"]*"[[:space:]]+"([^"]+)".*/\1/p' "$VALIDATE")

if [ "${#registered[@]}" -eq 0 ]; then
  echo "${VALIDATE##*/} registers no suite at all — the run_suite block is gone or its shape changed"
  echo "  this gate reads: run_suite \"<label>\" \"<path>\""
  exit 1
fi

# --- set arithmetic ---------------------------------------------------------------
has() {  # has <needle> <haystack...>
  local needle="$1"; shift
  local item
  for item in "$@"; do [ "$item" = "$needle" ] && return 0; done
  return 1
}

ci_only_paths=()
for entry in ${CI_ONLY[@]+"${CI_ONLY[@]}"}; do
  ci_only_paths+=("$(ci_only_path "$entry")")
done

# 1. on disk, not registered, not excused
for suite in "${on_disk[@]}"; do
  has "$suite" ${registered[@]+"${registered[@]}"} && continue
  has "$suite" ${ci_only_paths[@]+"${ci_only_paths[@]}"} && continue
  note "$suite runs in CI but not in validate.sh, and is not on the CI-only list"
  note "  add it to the run_suite block, or add it to CI_ONLY in ${BASH_SOURCE[0]##*/} with the reason validate.sh must not run it"
done

# 2. registered, not on disk — run_suite reports this too, but as "missing", which
#    reads like a broken path rather than a registration nothing backs.
for suite in "${registered[@]}"; do
  has "$suite" "${on_disk[@]}" && continue
  note "${VALIDATE##*/} registers $suite, which is not on disk"
done

# 3. an opt-out entry that outlived its suite, or that lost its reason
for entry in ${CI_ONLY[@]+"${CI_ONLY[@]}"}; do
  suite="$(ci_only_path "$entry")"
  reason="$(ci_only_reason "$entry")"
  if [ -z "$reason" ]; then
    note "$suite is on the CI-only list with no reason — an unexplained exemption is the silence this gate exists to remove"
  fi
  if ! has "$suite" "${on_disk[@]}"; then
    note "$suite is on the CI-only list but no longer exists — drop the entry"
    continue
  fi
  # 4. excused AND registered: the two halves of this file contradict each other,
  #    and the reason above is now a lie a reader would believe.
  if has "$suite" ${registered[@]+"${registered[@]}"}; then
    note "$suite is on the CI-only list AND registered in ${VALIDATE##*/} — validate.sh runs it, so the stated reason is false. Drop the CI-only entry."
  fi
done

if [ "$LIST" -eq 1 ]; then
  for suite in "${on_disk[@]}"; do
    if has "$suite" ${registered[@]+"${registered[@]}"}; then
      echo "registered   $suite"
    else
      reason=""
      for entry in ${CI_ONLY[@]+"${CI_ONLY[@]}"}; do
        [ "$(ci_only_path "$entry")" = "$suite" ] && reason="$(ci_only_reason "$entry")"
      done
      if [ -n "$reason" ]; then
        echo "ci-only      $suite — $reason"
      else
        echo "UNACCOUNTED  $suite"
      fi
    fi
  done
  exit 0
fi

[ "${#findings[@]}" -eq 0 ] && exit 0
printf '%s\n' "${findings[@]}"
exit 1
