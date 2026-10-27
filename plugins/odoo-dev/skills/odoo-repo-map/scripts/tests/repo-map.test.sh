#!/usr/bin/env bash
# repo-map.test.sh — repo-map.sh, project-resolve.sh and project-bootstrap.sh
# against the real `odoo-dev` CLI on a temp ODOO_DEV_HOME. Nothing is mocked.
#
# The CLI is found in this order: $ODOO_DEV_BIN (a directory holding odoo-dev),
# then <repo root>/.venv/bin. The tests never use a system-wide install.
# No network: the "remote" is a local bare repository.
#
# Usage: bash scripts/tests/repo-map.test.sh   (exit 0 = all pass)
set -uo pipefail

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(cd "$SCRIPTS/../../../../.." && pwd)"
PATH="${ODOO_DEV_BIN:-$ROOT/.venv/bin}:$PATH"
command -v odoo-dev >/dev/null || { echo "odoo-dev not found: run uv sync or set ODOO_DEV_BIN" >&2; exit 1; }
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export ODOO_DEV_HOME="$TMP/home" REPOS_DIR="" REPOS_DIR_CANDIDATES="$TMP/none"

pass=0; fail=0
check() { # check <name> <expected_exit> <actual_exit>
  if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: expected exit $2, got $3"; fi
}
check_eq() { # check_eq <name> <expected> <actual>
  if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: expected '$2', got '$3'"; fi
}
check_contains() { # check_contains <name> <needle> <haystack>
  case "$3" in *"$2"*) pass=$((pass+1)) ;; *) fail=$((fail+1)); echo "FAIL $1: '$2' not in output: $3" ;; esac
}
check_absent() { # check_absent <name> <needle> <haystack>
  case "$3" in *"$2"*) fail=$((fail+1)); echo "FAIL $1: '$2' unexpectedly in output: $3" ;; *) pass=$((pass+1)) ;; esac
}
map() { bash "$SCRIPTS/repo-map.sh" "$@"; }
resolve() { bash "$SCRIPTS/project-resolve.sh" "$@"; }
jget() { bash "$SCRIPTS/../../../scripts/json-get.sh" "$@"; }

# --- an empty home: nothing is created by reading ------------------------------
out="$(map list 2>&1)"; check "list-empty" 0 $?
[ ! -e "$ODOO_DEV_HOME" ]; check "read-creates-nothing" 0 $?
map get Nope >/dev/null 2>&1; check "get-unmapped" 3 $?
resolve Nope >/dev/null 2>&1; check "resolve-unmapped" 3 $?

# --- add: no folder needed -------------------------------------------------------
out="$(map add "Alpha" alpha --odoo-version 17.0 --branch-flow "task,UAT,main" --aliases "Alpha Care" 2>&1)"
check "add-without-folder" 0 $?
[ -f "$ODOO_DEV_HOME/repo-map.json" ]; check "add-creates-the-map" 0 $?
map add "Alpha" alpha --odoo-version 17.0 >/dev/null 2>&1; check "add-duplicate-refused" 2 $?
map add "NoVersion" nov >/dev/null 2>&1; check "add-needs-odoo-version" 2 $?
map add "Bad" bad --odoo-version 17 >/dev/null 2>&1; check "add-invalid-version" 4 $?
map add "Gamma" shared --odoo-version 17.0 >/dev/null 2>&1
map add "Delta" shared --odoo-version 18.0 >/dev/null 2>&1

# --- get / list ---------------------------------------------------------------------
out="$(map get Alpha --json)"; check "get" 0 $?
check_eq "get-repo" alpha "$(jget d.repo <<<"$out")"
check_eq "get-flow" "task,UAT,main" "$(jget 'd.branch_flow.join(",")' <<<"$out")"
check_eq "get-by-alias" Alpha "$(jget d.name <<<"$(map get "Alpha Care" --json)")"
check_eq "list-names" "Alpha,Delta,Gamma" "$(jget 'd.map((p) => p.name).join(",")' <<<"$(map list --json)")"
check_contains "list-text" "Alpha  alpha  17.0" "$(map list)"

# --- set merges ----------------------------------------------------------------------
map set Alpha --odoo-version 18.0 >/dev/null; check "set" 0 $?
out="$(map get Alpha --json)"
check_eq "set-changes-version" 18.0 "$(jget d.odoo_version <<<"$out")"
check_eq "set-keeps-repo" alpha "$(jget d.repo <<<"$out")"
check_eq "set-keeps-flow" "task,UAT,main" "$(jget 'd.branch_flow.join(",")' <<<"$out")"
check_eq "set-keeps-aliases" "Alpha Care" "$(jget 'd.aliases.join(",")' <<<"$out")"
map set Alpha --upgrade-branch 19.0=19-upgrade >/dev/null; check "set-upgrade-branch" 0 $?
map set Alpha --upgrade-branch 20.0=20-upgrade >/dev/null
check_eq "upgrade-branches-merge" "19.0=19-upgrade,20.0=20-upgrade" \
  "$(jget -l 'Object.entries(d.upgrade_branch).map(([k, v]) => k + "=" + v)' <<<"$(map get Alpha --json)" | paste -sd,)"
map set Alpha --upgrade-branch 20.0= >/dev/null
check_absent "upgrade-branch-removed" "20.0" "$(map get Alpha --json)"
map set Alpha --aliases "" >/dev/null
check_eq "set-aliases-empty-removes" "" "$(jget 'd.aliases.join(",")' <<<"$(map get Alpha --json)")"
map set Nope --odoo-version 18.0 >/dev/null 2>&1; check "set-unmapped" 3 $?
map set Alpha --branch-flow "task,task" >/dev/null 2>&1; check "set-invalid-flow" 4 $?
map set Alpha --bogus x >/dev/null 2>&1; check "set-unknown-option" 2 $?

# --- set-flow / set-release-owners ----------------------------------------------------
out="$(map set-flow Alpha "task,E2E,UAT,main" --flow-confirmed 2>&1)"; check "set-flow" 0 $?
check_contains "set-flow-note" "no longer recorded" "$out"
check_eq "set-flow-written" "task,E2E,UAT,main" "$(jget 'd.branch_flow.join(",")' <<<"$(map get Alpha --json)")"
map set-release-owners Alpha alice none >/dev/null; check "set-release-owners" 0 $?
out="$(map get Alpha --json)"
check_eq "owner-assignee" alice "$(jget d.release_assignee <<<"$out")"
check_eq "owner-reviewer" none "$(jget d.release_reviewer <<<"$out")"

# --- validate / remove -------------------------------------------------------------------
out="$(map validate --json)"; check "validate-clean" 0 $?
check_contains "validate-json" '"ok": true' "$out"
printf '{"projects": {"Broken": {"repo": "", "odoo_version": "x"}}}\n' > "$ODOO_DEV_HOME/repo-map.json"
map validate >/dev/null 2>&1; check "validate-invalid" 4 $?
map remove Broken >/dev/null 2>&1; check "remove-repairs-an-invalid-map" 0 $?
printf '{"projects": {}}\n' > "$ODOO_DEV_HOME/repo-map.json"
map add Alpha alpha --odoo-version 17.0 --branch-flow "task,UAT,main" --aliases "Alpha Care" >/dev/null
map add Beta beta --odoo-version 18.0 >/dev/null
map add Gamma shared --odoo-version 17.0 >/dev/null
map add Delta shared --odoo-version 18.0 >/dev/null
map remove Beta >/dev/null; check "remove" 0 $?
map remove Beta >/dev/null 2>&1; check "remove-unmapped" 3 $?
map get Beta >/dev/null 2>&1; check "removed-is-gone" 3 $?

# --- project-resolve.sh --------------------------------------------------------------------
origin="$TMP/origin.git"
git init --quiet --bare "$origin"
mkdir -p "$TMP/repos"
git init --quiet "$TMP/repos/alpha"
git -C "$TMP/repos/alpha" config user.email t@example.invalid
git -C "$TMP/repos/alpha" config user.name t
git -C "$TMP/repos/alpha" remote add origin "$origin"
git -C "$TMP/repos/alpha" -c core.hooksPath=/dev/null commit --quiet --allow-empty -m seed
git -C "$TMP/repos/alpha" branch -M UAT
git -C "$TMP/repos/alpha" push --quiet origin UAT
git init --quiet "$TMP/repos/other"
export REPOS_DIR="$TMP/repos"

out="$(resolve Alpha)"; check "resolve-by-name" 0 $?
check_eq "resolve-project" Alpha "$(jget d.project <<<"$out")"
check_eq "resolve-repo" alpha "$(jget d.repo <<<"$out")"
check_eq "resolve-repo-root" "$TMP/repos/alpha" "$(jget d.repo_root <<<"$out")"
check_eq "resolve-version" 17.0 "$(jget d.odoo_version <<<"$out")"
check_eq "resolve-flow" "task,UAT,main" "$(jget 'd.branch_flow.join(",")' <<<"$out")"
check_eq "base-branch-skips-missing" UAT "$(jget d.base_branch <<<"$out")"
check_eq "resolve-by-alias" Alpha "$(jget d.project <<<"$(resolve "Alpha Care")")"
check_eq "resolve-by-alias-ignores-case" Alpha "$(jget d.project <<<"$(resolve "alpha care")")"
check_eq "resolve-by-repo-folder" Alpha "$(jget d.project <<<"$(resolve alpha)")"
check_eq "resolve-defaults-flow" "task,E2E,UAT,main" "$(jget 'd.branch_flow.join(",")' <<<"$(resolve Gamma)")"
check_eq "no-checkout-no-base" "" "$(jget d.base_branch <<<"$(resolve Gamma)")"
check_eq "release-owners-null" "" "$(jget d.release_assignee <<<"$(resolve Gamma)")"

resolve shared >/dev/null 2>"$TMP/err"; check "resolve-ambiguous" 6 $?
check_contains "ambiguous-names-candidates" "Delta, Gamma" "$(cat "$TMP/err")"
resolve Nope >/dev/null 2>"$TMP/err"; check "resolve-unmapped-name" 3 $?
check_contains "unmapped-lists-projects" "Alpha" "$(cat "$TMP/err")"
resolve >/dev/null 2>&1; check "resolve-usage" 2 $?
resolve Alpha --series 19 >/dev/null 2>&1; check "resolve-bad-series" 2 $?
resolve Alpha --bogus >/dev/null 2>&1; check "resolve-bad-option" 2 $?

# --next-after walks branch_flow; the first hop is `task`.
check_eq "next-after-task" UAT "$(jget d.next_env <<<"$(resolve Alpha --next-after task)")"
check_eq "next-after-mid" main "$(jget d.next_env <<<"$(resolve Alpha --next-after UAT)")"
out="$(resolve Alpha --next-after main)"; check "next-after-production" 0 $?
check_eq "at-production" true "$(jget d.at_production <<<"$out")"
check_eq "production-has-no-next" "" "$(jget d.next_env <<<"$out")"
resolve Alpha --next-after nope >/dev/null 2>&1; check "next-after-unknown-branch" 4 $?

# --series fills upgrade_branch: the recorded branch, else the last flow element.
map set Alpha --upgrade-branch 19.0=19-upgrade >/dev/null
check_eq "series-recorded" 19-upgrade "$(jget d.upgrade_branch <<<"$(resolve Alpha --series 19.0)")"
check_eq "series-falls-back" main "$(jget d.upgrade_branch <<<"$(resolve Alpha --series 18.0)")"
check_eq "no-series-null" "" "$(jget d.upgrade_branch <<<"$(resolve Alpha)")"

# --- project-bootstrap.sh ----------------------------------------------------------------------
mkdir -p "$TMP/repos/newrepo/mod_a" "$TMP/repos/newrepo/mod_b"
printf "{'name': 'a', 'version': '17.0.1.0.0'}\n" > "$TMP/repos/newrepo/mod_a/__manifest__.py"
printf "{'name': 'b', 'version': '17.0.2.0.0'}\n" > "$TMP/repos/newrepo/mod_b/__manifest__.py"
git init --quiet "$TMP/repos/newrepo"
out="$(bash "$SCRIPTS/project-bootstrap.sh" "$TMP/repos/newrepo" --project "New Client" 2>/dev/null)"; check "bootstrap" 0 $?
check_eq "bootstrap-repo" newrepo "$(jget d.repo <<<"$out")"
check_eq "bootstrap-version" 17.0 "$(jget d.odoo_version <<<"$out")"
check_eq "bootstrap-flow-left-to-defaults" "" "$(jget 'd.branch_flow.join(",")' <<<"$out")"
check_eq "bootstrapped-resolves" "New Client" "$(jget d.project <<<"$(resolve newrepo)")"
bash "$SCRIPTS/project-bootstrap.sh" "$TMP/repos/newrepo" >/dev/null 2>&1; check "bootstrap-twice" 3 $?
git init --quiet "$TMP/repos/bare"
bash "$SCRIPTS/project-bootstrap.sh" "$TMP/repos/bare" >/dev/null 2>&1; check "bootstrap-no-version" 2 $?
bash "$SCRIPTS/project-bootstrap.sh" "$TMP/missing" >/dev/null 2>&1; check "bootstrap-no-folder" 2 $?

# --- no inline parsers ----------------------------------------------------------------------------
check_eq "no-node-e-in-the-wrappers" 0 "$(cat "$SCRIPTS"/repo-map.sh "$SCRIPTS"/project-*.sh "$SCRIPTS"/repos-dir.sh | grep -c 'node -e')"

echo "repo-map.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
