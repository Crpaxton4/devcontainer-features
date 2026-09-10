#!/usr/bin/env bash
# repo-map.test.sh — offline unit tests for repo-map.sh and project-resolve.sh.
# No network, no repos tree, no Odoo: every case runs against a temp map file.
#
# Usage: bash scripts/tests/repo-map.test.sh   (exit 0 = all pass)
set -uo pipefail

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0; fail=0
check() { # check <name> <expected_exit> <actual_exit>
  if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: expected exit $2, got $3"; fi
}
check_contains() { # check_contains <name> <needle> <haystack>
  case "$3" in *"$2"*) pass=$((pass+1)) ;; *) fail=$((fail+1)); echo "FAIL $1: '$2' not in output: $3" ;; esac
}

seed() {
  cat > "$TMP/map.json" <<'JSON'
{
  "_doc": "test fixture",
  "projects": {
    "Alpha": {"repo": "alpha", "default_branch": "UAT", "odoo_version": "17.0",
              "branch_flow": ["dev", "UAT", "main"], "flow_confirmed": true},
    "Beta": {"repo": "beta", "default_branch": "staging", "odoo_version": "18.0",
             "branch_flow": ["dev", "staging", "prod"]},
    "Gamma": {"repo": "shared", "default_branch": "dev"},
    "Delta": {"repo": "shared", "default_branch": "dev"}
  }
}
JSON
}
map() { REPO_MAP_FILE="$TMP/map.json" bash "$SCRIPTS/repo-map.sh" "$@"; }
resolve() { REPO_MAP_FILE="$TMP/map.json" REPOS_DIR="" bash "$SCRIPTS/project-resolve.sh" "$@"; }

# --- validation ---------------------------------------------------------------
seed
out="$(map validate 2>&1)"; check "validate-clean" 0 $?
check_contains "validate-json" '"ok": true' "$out"

# branch_flow must be an array, not the legacy free-text notes string.
seed && node -e '
  const fs=require("fs"); const f=process.argv[1];
  const d=JSON.parse(fs.readFileSync(f,"utf8")); d.projects.Alpha.branch_flow="dev -> UAT -> main";
  fs.writeFileSync(f, JSON.stringify(d));' "$TMP/map.json"
out="$(map validate 2>&1)"; check "flow-must-be-array" 4 $?
check_contains "flow-array-msg" "branch_flow must be an array" "$out"

# default_branch off the chain is the failure that silently mis-targets a PR.
seed && node -e '
  const fs=require("fs"); const f=process.argv[1];
  const d=JSON.parse(fs.readFileSync(f,"utf8")); d.projects.Alpha.default_branch="nope";
  fs.writeFileSync(f, JSON.stringify(d));' "$TMP/map.json"
out="$(map validate 2>&1)"; check "default-branch-on-chain" 4 $?
check_contains "default-branch-msg" "is not in branch_flow" "$out"

seed && node -e '
  const fs=require("fs"); const f=process.argv[1];
  const d=JSON.parse(fs.readFileSync(f,"utf8")); d.projects.Alpha.branch_flow=["dev","UAT","dev"];
  fs.writeFileSync(f, JSON.stringify(d));' "$TMP/map.json"
out="$(map validate 2>&1)"; check "flow-no-duplicates" 4 $?

seed && node -e '
  const fs=require("fs"); const f=process.argv[1];
  const d=JSON.parse(fs.readFileSync(f,"utf8")); d.projects.Beta.flow_confirmed="yes";
  fs.writeFileSync(f, JSON.stringify(d));' "$TMP/map.json"
out="$(map validate 2>&1)"; check "flow-confirmed-boolean" 4 $?

seed && node -e '
  const fs=require("fs"); const f=process.argv[1];
  const d=JSON.parse(fs.readFileSync(f,"utf8")); d.projects.Beta.hosting="odoo.sh";
  fs.writeFileSync(f, JSON.stringify(d));' "$TMP/map.json"
out="$(map validate 2>&1)"; check "unknown-key-rejected" 4 $?

# --- get / list ---------------------------------------------------------------
seed
out="$(map get Alpha)"; check "get-known" 0 $?
check_contains "get-carries-flow" '"branch_flow":["dev","UAT","main"]' "$out"
out="$(map get Nope 2>&1)"; check "get-unmapped" 3 $?

# --- set-flow -----------------------------------------------------------------
seed
out="$(map set-flow Beta "dev,staging,prod" --flow-confirmed)"; check "set-flow" 0 $?
check_contains "set-flow-confirms" '"flow_confirmed":true' "$out"
[ -f "$TMP/map.json.bak" ]; check "set-flow-writes-bak" 0 $?

# A rejected edit must never land: the live map keeps the prior content.
seed
before="$(cat "$TMP/map.json")"
map set-flow Alpha "dev,nowhere" >/dev/null 2>&1; check "set-flow-invalid-rejected" 4 $?
[ "$(cat "$TMP/map.json")" = "$before" ]; check "set-flow-invalid-no-write" 0 $?

# --- resolve ------------------------------------------------------------------
seed
out="$(resolve Alpha)"; check "resolve-by-project" 0 $?
out="$(resolve alpha)"; check "resolve-by-repo" 0 $?
check_contains "resolve-repo-finds-project" '"project":"Alpha"' "$out"
out="$(resolve shared 2>&1)"; check "resolve-ambiguous-repo" 3 $?
check_contains "resolve-ambiguous-lists" "Gamma" "$out"
out="$(resolve Nope 2>&1)"; check "resolve-unmapped" 3 $?
check_contains "resolve-unmapped-says-ask" "never guess" "$out"

out="$(resolve Alpha --next-after dev)"; check "next-after-mid-chain" 0 $?
check_contains "next-env" '"next_env":"UAT"' "$out"
out="$(resolve Alpha --next-after UAT)"; check "next-after-confirmed-prod" 0 $?
check_contains "next-env-prod" '"next_env":"main"' "$out"
out="$(resolve Alpha --next-after main)"; check "next-after-last" 0 $?
check_contains "at-production" '"at_production":true' "$out"

# Beta's flow is unconfirmed: a staging hop is allowed, the production hop is not.
out="$(resolve Beta --next-after dev)"; check "unconfirmed-staging-hop-ok" 0 $?
check_contains "unconfirmed-staging-next" '"next_env":"staging"' "$out"
out="$(resolve Beta --next-after staging 2>&1)"; check "unconfirmed-prod-hop-blocked" 4 $?
check_contains "unconfirmed-prod-msg" "unconfirmed" "$out"

out="$(resolve Gamma --next-after dev 2>&1)"; check "no-flow-blocked" 4 $?
out="$(resolve Alpha --next-after ghost 2>&1)"; check "branch-not-in-flow" 4 $?

# --- release_assignee / release_reviewer ---------------------------------------
# Recorded per project so the release route never has to ask mid-run. Present and
# usable, or absent and null — an empty one would read as answered and name nobody.

seed
out="$(map add "Epsilon" epsilon --no-repo-check --default-branch main \
        --release-assignee cpqoc --release-reviewer arhqoc)"; check "add-release-owners" 0 $?
out="$(map get Epsilon)"; check "get-after-add" 0 $?
check_contains "add-keeps-assignee" '"release_assignee":"cpqoc"' "$out"
check_contains "add-keeps-reviewer" '"release_reviewer":"arhqoc"' "$out"

# `none` is a real answer — a deliberate absence, not a missing one.
seed
map add "Zeta" zeta --no-repo-check --release-assignee cpqoc --release-reviewer none >/dev/null
out="$(map get Zeta)"; check_contains "reviewer-none-accepted" '"release_reviewer":"none"' "$out"

# An entry with neither field is still valid: absence is how "never recorded" is
# said, and the skill asks in that case.
seed
map add "Eta" eta --no-repo-check >/dev/null; check "add-without-release-owners" 0 $?

for field in release_assignee release_reviewer; do
  seed && FIELD="$field" node -e '
    const fs=require("fs"); const f=process.argv[1];
    const d=JSON.parse(fs.readFileSync(f,"utf8")); d.projects.Alpha[process.env.FIELD]="   ";
    fs.writeFileSync(f, JSON.stringify(d));' "$TMP/map.json"
  out="$(map validate 2>&1)"; check "$field-empty-rejected" 4 $?
  check_contains "$field-empty-msg" "must be a non-empty string" "$out"

  seed && FIELD="$field" node -e '
    const fs=require("fs"); const f=process.argv[1];
    const d=JSON.parse(fs.readFileSync(f,"utf8")); d.projects.Alpha[process.env.FIELD]=["cpqoc"];
    fs.writeFileSync(f, JSON.stringify(d));' "$TMP/map.json"
  out="$(map validate 2>&1)"; check "$field-non-string-rejected" 4 $?
done

# `add` only creates, so an already-mapped project needs the setter. Without it
# the fields would be unreachable for every project already in the map.
seed
out="$(map set-release-owners Alpha cpqoc arhqoc)"; check "set-release-owners" 0 $?
check_contains "set-owners-assignee" '"release_assignee":"cpqoc"' "$out"
check_contains "set-owners-reviewer" '"release_reviewer":"arhqoc"' "$out"
[ -f "$TMP/map.json.bak" ]; check "set-owners-writes-bak" 0 $?
out="$(map set-release-owners Nope a b 2>&1)"; check "set-owners-unmapped" 3 $?

# An empty assignee is rejected on the tmp file, so the live map never takes it.
seed
before="$(cat "$TMP/map.json")"
map set-release-owners Alpha "" arhqoc >/dev/null 2>&1; check "set-owners-empty-rejected" 4 $?
[ "$(cat "$TMP/map.json")" = "$before" ]; check "set-owners-empty-no-write" 0 $?

# project-resolve.sh is what the release skill actually reads, so the fields have
# to survive the trip through it — as values when set, as null when not.
seed
map add "Theta" theta --no-repo-check --default-branch main \
    --release-assignee cpqoc --release-reviewer none >/dev/null
out="$(resolve Theta)"; check "resolve-with-release-owners" 0 $?
check_contains "resolve-emits-assignee" '"release_assignee":"cpqoc"' "$out"
check_contains "resolve-emits-reviewer" '"release_reviewer":"none"' "$out"

out="$(resolve Alpha)"; check "resolve-without-release-owners" 0 $?
check_contains "resolve-assignee-null" '"release_assignee":null' "$out"
check_contains "resolve-reviewer-null" '"release_reviewer":null' "$out"

echo "{\"passed\": $pass, \"failed\": $fail}"
[ "$fail" -eq 0 ]
