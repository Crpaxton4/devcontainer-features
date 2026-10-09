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
# Removing a key is a distinct outcome from writing an empty one, so the absence
# has to be asserted directly rather than inferred from the value.
check_absent() { # check_absent <name> <needle> <haystack>
  case "$3" in *"$2"*) fail=$((fail+1)); echo "FAIL $1: '$2' unexpectedly in output: $3" ;; *) pass=$((pass+1)) ;; esac
}

seed() {
  cat > "$TMP/map.json" <<'JSON'
{
  "_doc": "test fixture",
  "projects": {
    "Alpha": {"repo": "alpha", "default_branch": "UAT", "odoo_version": "17.0",
              "branch_flow": [":task", "UAT", "main"], "flow_confirmed": true},
    "Beta": {"repo": "beta", "default_branch": "staging", "odoo_version": "18.0",
             "branch_flow": [":task", "staging", "prod"]},
    "Gamma": {"repo": "shared", "default_branch": "dev"},
    "Delta": {"repo": "shared", "default_branch": "dev"}
  }
}
JSON
}
map() { REPO_MAP_FILE="$TMP/map.json" bash "$SCRIPTS/repo-map.sh" "$@"; }
resolve() { REPO_MAP_FILE="$TMP/map.json" REPOS_DIR="" bash "$SCRIPTS/project-resolve.sh" "$@"; }
# Same two, with REPOS_DIR pinned: the repo_path cases turn on what the flat-tree
# layout would have said, so they cannot use the ambient value.
map_in() { local rd="$1"; shift; REPO_MAP_FILE="$TMP/map.json" REPOS_DIR="$rd" bash "$SCRIPTS/repo-map.sh" "$@"; }
resolve_in() { local rd="$1"; shift; REPO_MAP_FILE="$TMP/map.json" REPOS_DIR="$rd" bash "$SCRIPTS/project-resolve.sh" "$@"; }
# REPOS_DIR set but absent makes repos-dir.sh exit 1, which is the devcontainer
# case: no repos tree resolves at all and project-resolve.sh gets an empty one.
NO_TREE="$TMP/no-such-tree"

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
  const d=JSON.parse(fs.readFileSync(f,"utf8")); d.projects.Alpha.branch_flow=["staging","UAT","staging"];
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

# --- absent vs mangled (#906) ---------------------------------------------------
# The two cases are told apart on whether the file exists, never on whether
# parsing happens to fail. Absent = a machine bootstrap-state.sh has never run on:
# seed it and answer the query. Present-but-bad = a real map that got damaged:
# a hard exit 4 naming the file and the parser's own complaint, with the bytes on
# disk left exactly as they were - reseeding one would take every project's base
# branch with it.

# ODOO_DEV_STATE_DIR is pinned on every call here so a bug in the path the
# bootstrap targets can never reach the developer's real state dir.
map_at() { local f="$1"; shift; REPO_MAP_FILE="$f" ODOO_DEV_STATE_DIR="$TMP/state" bash "$SCRIPTS/repo-map.sh" "$@"; }

BOOT="$TMP/never-bootstrapped"
mkdir -p "$BOOT"
out="$(map_at "$BOOT/repo-map.json" list 2>/dev/null)"; check "absent-map-bootstraps" 0 $?
check_contains "absent-map-query-answers" "{}" "$out"
[ -f "$BOOT/repo-map.json" ]; check "absent-map-file-created" 0 $?
# The bootstrap reports what it did on its own stdout; that must not land in
# front of the JSON this command emits, or every caller parsing it breaks.
out="$(map_at "$BOOT/repo-map.json" validate 2>/dev/null)"; check "bootstrapped-map-validates" 0 $?
[ "$out" = '{"ok": true}' ]; check "bootstrap-report-off-stdout" 0 $?
# And the seeded map is a working map, not merely a parseable one.
out="$(map_at "$BOOT/repo-map.json" add Tau tau --no-repo-check --default-branch main)"
check "bootstrapped-map-accepts-add" 0 $?
check_contains "bootstrapped-map-add-lands" '"added": "Tau"' "$out"

# Present but not JSON: exit 4, naming the file and quoting the parser verbatim.
printf '%s\n' '{ "projects": ' > "$TMP/map.json"
before="$(cat "$TMP/map.json")"
parse_err="$(node -e 'try { JSON.parse(require("fs").readFileSync(process.argv[1], "utf8")); } catch (e) { console.log(e.message); }' "$TMP/map.json")"
out="$(map validate 2>&1)"; check "mangled-map-exit-4" 4 $?
check_contains "mangled-map-names-file" "$TMP/map.json" "$out"
check_contains "mangled-map-quotes-parser" "$parse_err" "$out"
[ "$(cat "$TMP/map.json")" = "$before" ]; check "mangled-map-not-reseeded" 0 $?
# Every command runs the same precheck, so a plain read is refused too.
out="$(map get Alpha 2>&1)"; check "mangled-map-get-exit-4" 4 $?
check_contains "mangled-map-get-names-file" "$TMP/map.json" "$out"

# Parses, but is not a map: the top-level key the script reads is missing. Same
# exit code and the same "which file?" answer, because the repair is the same one.
printf '%s\n' '{"_doc": "no projects key"}' > "$TMP/map.json"
out="$(map list 2>&1)"; check "map-missing-projects-exit-4" 4 $?
check_contains "map-missing-projects-msg" "$TMP/map.json: projects must be an object" "$out"

# A valid map is untouched by any of that: no bootstrap, no rewrite.
seed
before="$(cat "$TMP/map.json")"
out="$(map list)"; check "valid-map-still-lists" 0 $?
check_contains "valid-map-lists-projects" '"Alpha"' "$out"
[ "$(cat "$TMP/map.json")" = "$before" ]; check "valid-map-not-rewritten" 0 $?

# --- get / list ---------------------------------------------------------------
seed
out="$(map get Alpha)"; check "get-known" 0 $?
check_contains "get-carries-flow" '"branch_flow":[":task","UAT","main"]' "$out"
out="$(map get Nope 2>&1)"; check "get-unmapped" 3 $?

# --- set-flow -----------------------------------------------------------------
seed
out="$(map set-flow Beta ":task,staging,prod" --flow-confirmed)"; check "set-flow" 0 $?
check_contains "set-flow-confirms" '"flow_confirmed":true' "$out"
[ -f "$TMP/map.json.bak" ]; check "set-flow-writes-bak" 0 $?

# A rejected edit must never land: the live map keeps the prior content.
seed
before="$(cat "$TMP/map.json")"
map set-flow Alpha ":task,nowhere" >/dev/null 2>&1; check "set-flow-invalid-rejected" 4 $?
[ "$(cat "$TMP/map.json")" = "$before" ]; check "set-flow-invalid-no-write" 0 $?

# --- set ------------------------------------------------------------------------
# `add` only creates, so before `set` the only way to change a field was remove +
# full re-add: every field left off the re-add was silently dropped, and the
# remove committed its own write first, so a failed re-add left the project gone
# with the .bak already one generation past the original entry.

seed
map add "Sigma" sigma --no-repo-check --default-branch main --odoo-version 17.0 \
    --notes "release assignee is alice" --release-assignee cpqoc >/dev/null
before="$(cat "$TMP/map.json")"
out="$(map set Sigma --remote acme/sigma)"; check "set-one-field" 0 $?
check_contains "set-applies-remote" '"remote":"acme/sigma"' "$out"
# The headline of the bug report: notes is what remove + re-add silently dropped.
check_contains "set-keeps-notes" '"notes":"release assignee is alice"' "$out"
check_contains "set-keeps-version" '"odoo_version":"17.0"' "$out"
check_contains "set-keeps-branch" '"default_branch":"main"' "$out"
check_contains "set-keeps-release-owner" '"release_assignee":"cpqoc"' "$out"
# The .bak has to be the entry as it stood before this edit — that is the rollback
# point remove + re-add destroyed by committing the remove first.
[ "$(cat "$TMP/map.json.bak")" = "$before" ]; check "set-bak-is-prior-version" 0 $?

# Several fields at once, and the merge lands in the map, not just in the echo.
out="$(map set Sigma --odoo-version 18.0 --notes "chain caveat")"; check "set-many-fields" 0 $?
out="$(map get Sigma)"
check_contains "set-persists-version" '"odoo_version":"18.0"' "$out"
check_contains "set-persists-notes" '"notes":"chain caveat"' "$out"
check_contains "set-persists-earlier-remote" '"remote":"acme/sigma"' "$out"

# An empty value deletes the key: the one thing a merge cannot otherwise say, and
# an empty string left in place would read as an answer nobody gave.
out="$(map set Sigma --notes "")"; check "set-empty-unsets" 0 $?
check_absent "set-notes-removed" '"notes"' "$out"

out="$(map set Nope --remote x 2>&1)"; check "set-unmapped" 3 $?
out="$(map set Sigma 2>&1)"; check "set-needs-a-field" 2 $?
check_contains "set-needs-a-field-msg" "at least one field" "$out"
out="$(map set Sigma --remote 2>&1)"; check "set-flag-needs-value" 2 $?
out="$(map set Sigma --hosting odoo.sh 2>&1)"; check "set-unknown-option" 2 $?

# branch_flow and the release owners each already have a setter carrying its own
# extra question, so `set` points at them instead of duplicating the rules.
out="$(map set Sigma --branch-flow ":task,main" 2>&1)"; check "set-defers-branch-flow" 2 $?
check_contains "set-defers-branch-flow-msg" "set-flow" "$out"
out="$(map set Sigma --release-assignee bob 2>&1)"; check "set-defers-release-owners" 2 $?
check_contains "set-defers-release-owners-msg" "set-release-owners" "$out"

# A rejected edit must never land: default_branch off the chain fails on the tmp
# file, so the live map keeps the prior content.
seed
before="$(cat "$TMP/map.json")"
map set Alpha --default-branch nope >/dev/null 2>&1; check "set-invalid-rejected" 4 $?
[ "$(cat "$TMP/map.json")" = "$before" ]; check "set-invalid-no-write" 0 $?

# `add` stays create-only. `set` is the update path; overwriting through `add` is
# still an error, so a typo'd project name cannot quietly replace a real entry.
seed
out="$(map add Alpha alpha --no-repo-check --remote acme/alpha 2>&1)"; check "add-still-rejects-duplicate" 2 $?
check_contains "add-duplicate-msg" "duplicate project" "$out"

# --- resolve ------------------------------------------------------------------
seed
out="$(resolve Alpha)"; check "resolve-by-project" 0 $?
out="$(resolve alpha)"; check "resolve-by-repo" 0 $?
check_contains "resolve-repo-finds-project" '"project":"Alpha"' "$out"
# Unmapped and ambiguous were one exit code until #995 and have opposite
# remedies, so they are separate codes: 3 stays unmapped (agreeing with
# `repo-map.sh get`), ambiguous moved to 6. 5 is release-manifest.sh's.
out="$(resolve shared 2>&1)"; check "resolve-ambiguous-repo" 6 $?
check_contains "resolve-ambiguous-lists" "Gamma" "$out"
check_contains "resolve-ambiguous-says-ambiguous" "ambiguous" "$out"
out="$(resolve Nope 2>&1)"; check "resolve-unmapped" 3 $?
check_contains "resolve-unmapped-says-ask" "never guess" "$out"
check_contains "resolve-unmapped-points-at-bootstrap" "project-bootstrap.sh" "$out"

out="$(resolve Alpha --next-after :task)"; check "next-after-mid-chain" 0 $?
check_contains "next-env" '"next_env":"UAT"' "$out"
out="$(resolve Alpha --next-after UAT)"; check "next-after-confirmed-prod" 0 $?
check_contains "next-env-prod" '"next_env":"main"' "$out"
out="$(resolve Alpha --next-after main)"; check "next-after-last" 0 $?
check_contains "at-production" '"at_production":true' "$out"

# Beta's flow is unconfirmed: a staging hop is allowed, the production hop is not.
out="$(resolve Beta --next-after :task)"; check "unconfirmed-staging-hop-ok" 0 $?
check_contains "unconfirmed-staging-next" '"next_env":"staging"' "$out"
out="$(resolve Beta --next-after staging 2>&1)"; check "unconfirmed-prod-hop-blocked" 4 $?
check_contains "unconfirmed-prod-msg" "unconfirmed" "$out"

out="$(resolve Gamma --next-after :task 2>&1)"; check "no-flow-blocked" 4 $?
out="$(resolve Alpha --next-after ghost 2>&1)"; check "branch-not-in-flow" 4 $?
# A task branch is not an element of the chain, so the refusal names the token
# that IS — otherwise the first hop has no sayable answer.
check_contains "branch-not-in-flow-hints-token" "--next-after :task" "$out"

# --- branch_flow index 0 --------------------------------------------------------
# The chain's first element is where task work starts from, and that is usually a
# branch cut fresh per task: it exists on no remote. The old convention wrote the
# literal "dev" there, which every consumer walking the chain believed in and none
# could resolve (gh api .../branches/dev -> 404 on every mapped remote). ":task" is
# reserved for it instead: a colon is illegal anywhere in a git ref name, so the
# token cannot collide with a branch anyone could create.

# Reserved at element 0 and nowhere else — later elements are what work is
# promoted INTO, and there is nothing to promote into a per-task branch.
seed && node -e '
  const fs=require("fs"); const f=process.argv[1];
  const d=JSON.parse(fs.readFileSync(f,"utf8")); d.projects.Alpha.branch_flow=["UAT",":task","main"];
  fs.writeFileSync(f, JSON.stringify(d));' "$TMP/map.json"
out="$(map validate 2>&1)"; check "task-token-only-first" 4 $?
check_contains "task-token-only-first-msg" "only ever the FIRST element" "$out"

# A task PR needs a real branch to target, so default_branch is never the token.
seed && node -e '
  const fs=require("fs"); const f=process.argv[1];
  const d=JSON.parse(fs.readFileSync(f,"utf8")); d.projects.Alpha.default_branch=":task";
  fs.writeFileSync(f, JSON.stringify(d));' "$TMP/map.json"
out="$(map validate 2>&1)"; check "default-branch-not-token" 4 $?
check_contains "default-branch-not-token-msg" "per-task placeholder" "$out"

# Every element but the placeholder has to be a branch someone could actually
# create — that is what makes "placeholder or branch?" answerable by looking,
# rather than by special-casing whatever happens to sit at index 0.
for bad in "UAT staging" "re:lease" "ma*in" "feat..x" "main.lock"; do
  seed && BAD="$bad" node -e '
    const fs=require("fs"); const f=process.argv[1];
    const d=JSON.parse(fs.readFileSync(f,"utf8"));
    d.projects.Alpha.branch_flow=[":task", process.env.BAD, "main"]; d.projects.Alpha.default_branch="main";
    fs.writeFileSync(f, JSON.stringify(d));' "$TMP/map.json"
  out="$(map validate 2>&1)"; check "flow-element-not-a-branch-$bad" 4 $?
done

# The token is optional: a project whose task work is based directly on a shared
# environment records that branch first and no placeholder at all.
seed && node -e '
  const fs=require("fs"); const f=process.argv[1];
  const d=JSON.parse(fs.readFileSync(f,"utf8"));
  d.projects.Alpha.branch_flow=["staging","main"]; d.projects.Alpha.default_branch="staging";
  fs.writeFileSync(f, JSON.stringify(d));' "$TMP/map.json"
out="$(map validate 2>&1)"; check "no-placeholder-is-valid" 0 $?
check_absent "no-placeholder-no-warning" "warnings" "$out"

# A chain of nothing but the placeholder has no target, so the existing
# source-and-target rule covers it and no new one is needed.
seed && node -e '
  const fs=require("fs"); const f=process.argv[1];
  const d=JSON.parse(fs.readFileSync(f,"utf8"));
  d.projects.Alpha.branch_flow=[":task"]; delete d.projects.Alpha.default_branch;
  fs.writeFileSync(f, JSON.stringify(d));' "$TMP/map.json"
out="$(map validate 2>&1)"; check "placeholder-alone-rejected" 4 $?

# The phantom itself: valid, but the first element claims a branch nobody bases
# work on. A warning rather than a failure — the map is still usable, every other
# rule still holds, and the repair is one set-flow away, so it is quoted verbatim.
seed && node -e '
  const fs=require("fs"); const f=process.argv[1];
  const d=JSON.parse(fs.readFileSync(f,"utf8")); d.projects.Alpha.branch_flow=["dev","UAT","main"];
  fs.writeFileSync(f, JSON.stringify(d));' "$TMP/map.json"
out="$(map validate 2>&1)"; check "phantom-first-element-still-valid" 0 $?
check_contains "phantom-warns" '"warnings"' "$out"
check_contains "phantom-warns-names-it" 'branch_flow starts with \"dev\"' "$out"
check_contains "phantom-warns-carries-fix" 'set-flow \"Alpha\" \":task,UAT,main\"' "$out"
# Silence is the contract for a clean map: the object stays byte-identical.
seed
out="$(map validate 2>&1)"; check "clean-map-no-warnings" 0 $?
[ "$out" = '{"ok": true}' ]; check "clean-map-object-unchanged" 0 $?

# Recording the phantom says so at the moment it is written, not only on demand:
# the mutation path forwards the warning to stderr without blocking the write.
seed
out="$(map set-flow Alpha "dev,UAT,main" 2>&1 >/dev/null)"; check "set-flow-phantom-allowed" 0 $?
check_contains "set-flow-phantom-warns" "neither the \":task\" placeholder" "$out"
out="$(map set-flow Alpha ":task,UAT,main" 2>&1 >/dev/null)"; check "set-flow-token-silent" 0 $?
check_absent "set-flow-token-no-warning" "branch_flow starts with" "$out"
out="$(map get Alpha)"; check_contains "set-flow-token-round-trips" '"branch_flow":[":task","UAT","main"]' "$out"

# --next-after answers the first hop from the token, with the same arithmetic as
# every other hop: the element after it is what a task PR targets, default_branch.
seed
out="$(resolve Alpha --next-after :task)"; check "next-after-token" 0 $?
check_contains "next-after-token-is-default-branch" '"next_env":"UAT"' "$out"
check_contains "next-after-token-not-production" '"at_production":false' "$out"

# Two-element chain: the first hop IS production, so an unconfirmed chain refuses
# it — the placeholder changes where the hop starts, never what is protected.
seed
map set Alpha --default-branch main >/dev/null
map set-flow Alpha ":task,main" >/dev/null
out="$(resolve Alpha --next-after :task 2>&1)"; check "token-first-hop-into-prod-blocked" 4 $?
check_contains "token-first-hop-prod-msg" "unconfirmed" "$out"

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

# --- repo_path ------------------------------------------------------------------
# The flat repos tree is a convention repos-dir.sh cannot satisfy for a checkout
# whose folder name can never equal `repo` — the Odoo devcontainer bind-mounts
# the one client repo at /mnt/extra-addons, a name fixed by the addons path. An
# absolute repo_path answers the lookup with no tree resolved at all.
#
# Two checkouts, so precedence is observable: one where the flat layout says to
# look, one the entry pins. A bare `.git` directory is enough for the presence
# check and keeps these cases independent of a git binary.
mkdir -p "$TMP/tree/iota/.git" "$TMP/mount/extra-addons/.git" "$TMP/mount-no-checkout"

seed
map add "Iota" iota --repo-path "$TMP/mount/extra-addons" --default-branch main >/dev/null
check "add-with-repo-path" 0 $?
out="$(map get Iota)"; check_contains "add-records-repo-path" "\"repo_path\":\"$TMP/mount/extra-addons\"" "$out"

out="$(resolve_in "$TMP/tree" Iota)"; check "resolve-with-repo-path" 0 $?
check_contains "repo-path-beats-repos-dir" "\"repo_path\":\"$TMP/mount/extra-addons\"" "$out"

# The reported bug verbatim: no tree resolves, yet the pinned checkout is found.
out="$(resolve_in "$NO_TREE" Iota)"; check "resolve-repo-path-without-tree" 0 $?
check_contains "repo-path-without-tree" "\"repo_path\":\"$TMP/mount/extra-addons\"" "$out"

# Without the override the same entry falls back to $REPOS_DIR/$repo, unchanged.
seed
map add "Iota" iota --no-repo-check --default-branch main >/dev/null
out="$(resolve_in "$TMP/tree" Iota)"; check "resolve-without-repo-path" 0 $?
check_contains "fallback-to-repos-dir" "\"repo_path\":\"$TMP/tree/iota\"" "$out"

# A pinned path with no checkout under it is still null: repo_path says where to
# look, not that something is there.
seed
map add "Nu" nu --repo-path "$TMP/mount-no-checkout" >/dev/null
out="$(resolve_in "$NO_TREE" Nu)"; check "resolve-repo-path-no-checkout" 0 $?
check_contains "repo-path-null-without-checkout" '"repo_path":null' "$out"

# add's repo-existence check reads the pinned path, so it needs neither a
# resolvable tree nor --no-repo-check — the workaround the bug report describes.
seed
out="$(map_in "$NO_TREE" add "Kappa" kappa --repo-path "$TMP/mount/extra-addons" 2>&1)"
check "add-repo-check-uses-repo-path" 0 $?
out="$(map_in "$NO_TREE" add "Lambda" lambda --repo-path "$TMP/nowhere" 2>&1)"
check "add-repo-path-missing-dir" 2 $?
check_contains "add-repo-path-missing-msg" "repo folder not found" "$out"

# Relative is rejected where the user typed it, naming the flag.
seed
out="$(map add "Mu" mu --repo-path relative/path 2>&1)"; check "add-repo-path-relative" 2 $?
check_contains "add-repo-path-relative-msg" "must be an absolute path" "$out"

# The key was previously rejected outright as unknown, which is what made the
# devcontainer unfixable from the map.
seed && node -e '
  const fs=require("fs"); const f=process.argv[1];
  const d=JSON.parse(fs.readFileSync(f,"utf8")); d.projects.Alpha.repo_path="/mnt/extra-addons";
  fs.writeFileSync(f, JSON.stringify(d));' "$TMP/map.json"
out="$(map validate 2>&1)"; check "repo-path-absolute-accepted" 0 $?

seed && node -e '
  const fs=require("fs"); const f=process.argv[1];
  const d=JSON.parse(fs.readFileSync(f,"utf8")); d.projects.Alpha.repo_path="extra-addons";
  fs.writeFileSync(f, JSON.stringify(d));' "$TMP/map.json"
out="$(map validate 2>&1)"; check "repo-path-relative-rejected" 4 $?
check_contains "repo-path-relative-validate-msg" "must be an absolute path" "$out"

seed && node -e '
  const fs=require("fs"); const f=process.argv[1];
  const d=JSON.parse(fs.readFileSync(f,"utf8")); d.projects.Alpha.repo_path="   ";
  fs.writeFileSync(f, JSON.stringify(d));' "$TMP/map.json"
out="$(map validate 2>&1)"; check "repo-path-empty-rejected" 4 $?
check_contains "repo-path-empty-msg" "must be a non-empty string" "$out"

seed && node -e '
  const fs=require("fs"); const f=process.argv[1];
  const d=JSON.parse(fs.readFileSync(f,"utf8")); d.projects.Alpha.repo_path=["/mnt/extra-addons"];
  fs.writeFileSync(f, JSON.stringify(d));' "$TMP/map.json"
out="$(map validate 2>&1)"; check "repo-path-non-string-rejected" 4 $?

# `set` carries repo_path for the same reason every other field needs a setter:
# the devcontainer's pinned checkout was otherwise unreachable on a project that
# was already in the map, which is every project.
seed
map add "Rho" iota --no-repo-check --default-branch main >/dev/null
out="$(map_in "$NO_TREE" set Rho --repo-path "$TMP/mount/extra-addons")"; check "set-repo-path" 0 $?
check_contains "set-records-repo-path" "\"repo_path\":\"$TMP/mount/extra-addons\"" "$out"
out="$(resolve_in "$NO_TREE" Rho)"; check "set-repo-path-resolves" 0 $?
check_contains "set-repo-path-beats-tree" "\"repo_path\":\"$TMP/mount/extra-addons\"" "$out"

# Same rules as on `add`, checked at the flag so the message names what was typed.
out="$(map set Rho --repo-path relative/path 2>&1)"; check "set-repo-path-relative" 2 $?
check_contains "set-repo-path-relative-msg" "must be an absolute path" "$out"
out="$(map set Rho --repo-path "$TMP/nowhere" 2>&1)"; check "set-repo-path-missing-dir" 2 $?
check_contains "set-repo-path-missing-msg" "repo folder not found" "$out"
out="$(map_in "$NO_TREE" set Rho --repo-path "$TMP/nowhere" --no-repo-check)"
check "set-repo-path-no-repo-check" 0 $?

# Unsetting puts the entry back on the flat tree — nothing else could do that
# without a remove + re-add that would have dropped every other field with it.
out="$(map set Rho --repo-path "")"; check "set-unsets-repo-path" 0 $?
check_absent "set-repo-path-removed" '"repo_path"' "$out"
out="$(resolve_in "$TMP/tree" Rho)"; check "resolve-after-repo-path-unset" 0 $?
check_contains "unset-repo-path-falls-back" "\"repo_path\":\"$TMP/tree/iota\"" "$out"

# --- one checkout, one entry ------------------------------------------------------
# Two entries may share `repo` (Gamma and Delta do, deliberately) and resolving by
# that folder is reported ambiguous. Two entries sharing repo_path is a different
# thing: the path IS the checkout, so nothing downstream can tell them apart, and
# field reports showed it being created by hand. Rejected at the two write sites,
# naming the flag and the entry already holding the path (#995).
seed
map add "Upsilon" upsilon --repo-path "$TMP/mount/extra-addons" >/dev/null
check "collide-first-add-ok" 0 $?
out="$(map add "Phi" phi --repo-path "$TMP/mount/extra-addons" 2>&1)"
check "add-rejects-colliding-repo-path" 2 $?
check_contains "add-collision-names-flag" "--repo-path" "$out"
check_contains "add-collision-names-holder" "Upsilon" "$out"

# `set --repo-path` is the other way one could be written.
map add "Chi" chi --no-repo-check >/dev/null
out="$(map set "Chi" --repo-path "$TMP/mount/extra-addons" 2>&1)"
check "set-rejects-colliding-repo-path" 2 $?
check_contains "set-collision-names-holder" "Upsilon" "$out"

# Re-setting a path to the value the SAME entry already holds is not a collision:
# an idempotent write must stay legal, or a re-run of a bootstrap becomes an error.
out="$(map set "Upsilon" --repo-path "$TMP/mount/extra-addons" 2>&1)"
check "set-same-entry-same-path-ok" 0 $?

# The rule lives at the write sites, NOT in the validator: `remove` is how a map
# that already collides gets repaired, and a validator rejecting it would make
# that repair impossible by any sanctioned command.
seed && node -e '
  const fs=require("fs"); const f=process.argv[1];
  const d=JSON.parse(fs.readFileSync(f,"utf8"));
  d.projects.Gamma.repo_path="/mnt/extra-addons"; d.projects.Delta.repo_path="/mnt/extra-addons";
  fs.writeFileSync(f, JSON.stringify(d));' "$TMP/map.json"
out="$(map validate 2>&1)"; check "validate-tolerates-existing-collision" 0 $?
out="$(map remove Delta 2>&1)"; check "remove-repairs-collision" 0 $?

# --- remove works on a semantically invalid map ------------------------------------
# The #992 papercut: `remove` pre-validated the live map, so an entry carrying an
# unknown key could not be deleted by the one sanctioned command that deletes —
# leaving the forbidden hand-edit as the only way out. The RESULT is still
# validated by commit_tmp, which is what keeps the guarantee that mattered.
seed && node -e '
  const fs=require("fs"); const f=process.argv[1];
  const d=JSON.parse(fs.readFileSync(f,"utf8")); d.projects.Alpha.bogus_key="whatever";
  fs.writeFileSync(f, JSON.stringify(d));' "$TMP/map.json"
out="$(map validate 2>&1)"; check "invalid-map-fails-validate" 4 $?
out="$(map remove Alpha 2>&1)"; check "remove-on-invalid-map" 0 $?
out="$(map validate 2>&1)"; check "remove-left-valid-map" 0 $?

# Removing something else leaves the map still invalid, so commit_tmp rejects the
# write: the attempt is allowed, landing a bad map is not.
seed && node -e '
  const fs=require("fs"); const f=process.argv[1];
  const d=JSON.parse(fs.readFileSync(f,"utf8")); d.projects.Alpha.bogus_key="whatever";
  fs.writeFileSync(f, JSON.stringify(d));' "$TMP/map.json"
before="$(cat "$TMP/map.json")"
map remove Beta >/dev/null 2>&1; check "remove-still-rejects-invalid-result" 4 $?
[ "$(cat "$TMP/map.json")" = "$before" ]; check "remove-rejected-no-write" 0 $?

# A map that does not PARSE is still exit 4 naming the file (#947): a delete
# against bytes nobody can read would be a guess at what the map used to say.
printf '{"projects": {' > "$TMP/map.json"
out="$(map remove Alpha 2>&1)"; check "remove-on-mangled-map" 4 $?
check_contains "remove-mangled-names-file" "invalid JSON" "$out"

# Unmapped is still 3, not swallowed by the dropped pre-check.
seed
out="$(map remove Nope 2>&1)"; check "remove-unmapped-still-3" 3 $?

# The origin sniff is gated on the checkout being present, so before repo_path it
# could never fire in the devcontainer and `remote` was always null. Needs a real
# git repo; skipped rather than failed where git is absent.
if command -v git >/dev/null 2>&1; then
  git init -q "$TMP/gitmount" >/dev/null 2>&1
  git -C "$TMP/gitmount" remote add origin git@github.com:acme/pinned-addons.git
  seed
  map add "Omicron" omicron --repo-path "$TMP/gitmount" >/dev/null
  out="$(resolve_in "$NO_TREE" Omicron)"; check "resolve-sniffs-origin-at-repo-path" 0 $?
  check_contains "repo-path-drives-origin-sniff" '"remote":"acme/pinned-addons"' "$out"

  # An explicit remote still wins: the map is what a human vouched for.
  seed
  map add "Pi" pi --repo-path "$TMP/gitmount" --remote other/override >/dev/null
  out="$(resolve_in "$NO_TREE" Pi)"; check_contains "map-remote-beats-sniff" '"remote":"other/override"' "$out"
fi

# --- project-bootstrap.sh ---------------------------------------------------------
# An unmapped project used to end the release route with nothing done, and the
# entry then got written by hand (#995). Everything but the branch chain is
# readable off the checkout, so it is recorded without a human — and the chain is
# left unset precisely so the exit-4 guard still demands one.
#
# Needs a real git repo with a manifest in it; skipped rather than failed where
# git is absent. Nothing here touches the network.
bootstrap() { REPO_MAP_FILE="$TMP/map.json" bash "$SCRIPTS/project-bootstrap.sh" "$@"; }

if command -v git >/dev/null 2>&1; then
  CO="$TMP/checkout/client-addons"
  mkdir -p "$CO/sale_ext" "$CO/stock_ext"
  printf "{\n 'name': 'Sale Ext',\n 'version': '18.0.1.0.0',\n}\n" > "$CO/sale_ext/__manifest__.py"
  printf "{\n 'name': 'Stock Ext',\n 'version': '18.0.2.1.0',\n}\n" > "$CO/stock_ext/__manifest__.py"
  git init -q -b staging "$CO" >/dev/null 2>&1
  git -C "$CO" remote add origin https://github.com/acme/client-addons.git
  # No commit on purpose: `git init` leaves an unborn branch, the branch name is
  # still readable with symbolic-ref, and committing here would run whatever
  # hooks the ambient machine has configured.

  seed
  out="$(bootstrap "$CO" 2>/dev/null)"; check "bootstrap-writes-entry" 0 $?
  check_contains "bootstrap-project-defaults-to-repo" '"project":"client-addons"' "$out"
  check_contains "bootstrap-repo-is-basename" '"repo":"client-addons"' "$out"
  check_contains "bootstrap-repo-path-absolute" "\"repo_path\":\"$CO\"" "$out"
  check_contains "bootstrap-remote-from-origin" '"remote":"acme/client-addons"' "$out"
  check_contains "bootstrap-version-from-manifests" '"odoo_version":"18.0"' "$out"
  check_contains "bootstrap-default-branch-from-head" '"default_branch":"staging"' "$out"
  # The whole point: the chain is NOT derived, so flow_confirmed stays false and
  # project-resolve.sh still stops a promotion at the exit-4 guard.
  check_absent "bootstrap-leaves-flow-unset" '"branch_flow"' "$out"
  check_absent "bootstrap-leaves-flow-unconfirmed" '"flow_confirmed"' "$out"
  out="$(resolve_in "$NO_TREE" client-addons)"; check "bootstrap-entry-resolves" 0 $?
  check_contains "bootstrap-entry-flow-false" '"flow_confirmed":false' "$out"
  out="$(resolve_in "$NO_TREE" client-addons --next-after staging 2>&1)"
  check "bootstrap-entry-still-stops-at-flow-guard" 4 $?
  check_contains "bootstrap-entry-hands-over-set-flow" "set-flow" "$out"

  # Re-running against a checkout an entry already pins is exit 3 with that
  # entry printed, never a second colliding entry — the field failure in #995.
  out="$(bootstrap "$CO" 2>/dev/null)"; check "bootstrap-already-mapped" 3 $?
  check_contains "bootstrap-already-mapped-names-project" '"project":"client-addons"' "$out"
  out="$(map list)"; check_contains "bootstrap-did-not-duplicate" '"client-addons"' "$out"

  # --project overrides the default, because the exact Odoo project.project name
  # is the map's key and a folder name is rarely it.
  seed
  out="$(bootstrap "$CO" --project "ACME Support" 2>/dev/null)"; check "bootstrap-project-flag" 0 $?
  check_contains "bootstrap-project-flag-key" '"project":"ACME Support"' "$out"

  # No manifest anywhere and a branch not named for a series: the field is omitted
  # rather than guessed.
  BARE="$TMP/checkout/bare-repo"
  mkdir -p "$BARE"
  git init -q -b feature/x "$BARE" >/dev/null 2>&1
  seed
  out="$(bootstrap "$BARE" 2>/dev/null)"; check "bootstrap-no-manifest" 0 $?
  check_absent "bootstrap-omits-unknown-version" '"odoo_version"' "$out"
  check_absent "bootstrap-omits-remote-without-origin" '"remote"' "$out"

  # Branch named for the series is the one other mechanical source.
  VER="$TMP/checkout/ver-repo"
  mkdir -p "$VER"
  git init -q -b 17.0 "$VER" >/dev/null 2>&1
  seed
  out="$(bootstrap "$VER" 2>/dev/null)"; check "bootstrap-version-from-branch" 0 $?
  check_contains "bootstrap-version-from-branch-value" '"odoo_version":"17.0"' "$out"

  # A path that is not a git checkout is a usage error: repo, remote and branch
  # all come from git, so there is nothing to derive.
  seed
  out="$(bootstrap "$TMP/mount-no-checkout" 2>&1)"; check "bootstrap-not-a-git-checkout" 2 $?
  check_contains "bootstrap-not-git-msg" "not a git checkout" "$out"
  out="$(bootstrap "$TMP/nowhere" 2>&1)"; check "bootstrap-missing-path" 2 $?
  out="$(bootstrap 2>&1)"; check "bootstrap-no-args" 2 $?

  # A duplicate project NAME is repo-map.sh's own exit 2, passed through.
  seed
  map add "client-addons" other --no-repo-check >/dev/null
  out="$(bootstrap "$CO" 2>&1)"; check "bootstrap-duplicate-project-name" 2 $?
  check_contains "bootstrap-duplicate-msg" "duplicate project" "$out"
fi

# --- the scratch file a write lands through --------------------------------------
# Every mutation writes a scratch file, validates it, and renames it onto the map.
# Two things about that file were once wrong and are asserted here: it must never
# survive the run, and its path must not be the one fixed name every concurrent
# writer would also be holding.
no_scratch() { [ -z "$(find "$TMP" -maxdepth 1 -name 'map.json.tmp*')" ]; }

seed
map set-flow Beta ":task,staging,prod" >/dev/null; check "scratch-write-ok" 0 $?
no_scratch; check "scratch-gone-after-success" 0 $?

# `set -euo pipefail` aborts commit_tmp at the failed validation, before the
# rename — so without a trap the rejected map is left sitting beside the live one,
# and the next run of the same subcommand inherits a stale file at that path.
seed
map set-flow Alpha ":task,nowhere" >/dev/null 2>&1; check "scratch-rejected-write" 4 $?
no_scratch; check "scratch-gone-after-rejection" 0 $?

# Here the failure is earlier still: node exits 3 before writing anything, so the
# scratch file is empty at the point the run dies. Empty or not, it is a file.
seed
map set Nope --remote x >/dev/null 2>&1; check "scratch-unmapped-exit" 3 $?
no_scratch; check "scratch-gone-after-unmapped" 0 $?

# A fixed "$MAP.tmp" is a name a second writer holds too. Occupying that exact
# path with a directory is the deterministic proof it is no longer used: a writer
# that still wrote there would die on EISDIR instead of committing.
seed
rm -f "$TMP/map.json.tmp"   # nothing should be there; a leak would hide this case
mkdir -p "$TMP/map.json.tmp"
out="$(map set-flow Beta ":task,staging,prod")"; check "scratch-path-not-fixed" 0 $?
check_contains "scratch-path-not-fixed-write" '"branch_flow":[":task","staging","prod"]' "$out"
rmdir "$TMP/map.json.tmp"

# Concurrent writers are deliberately not serialised: the last rename wins and the
# losing update is gone, which the script header now says outright. What must not
# happen is what one shared scratch path caused — a writer validating or renaming
# a file another writer had already replaced, so an invocation fails outright or
# the live map ends up being neither writer's JSON.
seed
pids=""
for n in 1 2 3 4 5 6; do
  map set Alpha --notes "writer $n" >/dev/null 2>&1 &
  pids="$pids $!"
done
racers=0
for p in $pids; do wait "$p" || racers=$((racers+1)); done
check "concurrent-writers-all-exit-0" 0 "$racers"
map validate >/dev/null 2>&1; check "concurrent-map-still-validates" 0 $?
no_scratch; check "concurrent-no-scratch-left" 0 $?

echo "{\"passed\": $pass, \"failed\": $fail}"
[ "$fail" -eq 0 ]
