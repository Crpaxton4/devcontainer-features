#!/usr/bin/env bash
# release-manifest.test.sh — offline tests for release-manifest.sh.
#
# A stub `gh` answers the two API calls from a fixture keyed by commit sha, over
# a real throwaway git repo. What is being tested is the attribution logic: which
# task id a PR gets, from which source, and which PRs are left unresolved. That
# is the part a wrong answer makes client-visible — a release note posted to a
# guessed task id cannot be unsent.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$(cd "$SCRIPT_DIR/.." && pwd)/release-manifest.sh"
work="$(mktemp -d "${TMPDIR:-/tmp}/release-manifest-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
expect() { if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted '$3', got '$2'" >&2; fi; }
q() { node -e '
  const j = JSON.parse(process.argv[1]);
  const v = new Function("m", "return " + process.argv[2])(j);
  console.log(typeof v === "object" ? JSON.stringify(v) : String(v));
' "$1" "$2"; }

# ---------- stub gh -------------------------------------------------------------
bin="$work/bin"; mkdir -p "$bin"
cat > "$bin/gh" <<'STUB'
#!/usr/bin/env bash
# gh api repos/<slug>/commits/<sha>/pulls --jq ...   -> one JSON line per PR
# gh api repos/<slug>/pulls/<n>/files    --paginate --jq ... -> one path per line
case "$2" in
  */commits/*/pulls)
    sha="${2#*/commits/}"; sha="${sha%/pulls}"
    [ -f "$GH_FIXTURES/commit-$sha.jsonl" ] && cat "$GH_FIXTURES/commit-$sha.jsonl"
    exit 0 ;;
  */pulls/*/files)
    n="${2#*/pulls/}"; n="${n%/files}"
    [ -f "$GH_FIXTURES/files-$n.txt" ] && cat "$GH_FIXTURES/files-$n.txt"
    exit 0 ;;
esac
exit 0
STUB
chmod +x "$bin/gh"
fx="$work/fixtures"; mkdir -p "$fx"

# ---------- throwaway repo with a from/to pair ----------------------------------
repo="$work/repo"; mkdir -p "$work/no-hooks"
origin="$work/origin.git"; git init --quiet --bare "$origin"
git init --quiet "$repo"
git -C "$repo" config user.email t@e.invalid
git -C "$repo" config user.name t
git -C "$repo" config core.hooksPath "$work/no-hooks"
git -C "$repo" remote add origin "$origin"
echo base > "$repo/f"
# Modules present on the TARGET branch, so the delta can add, update and remove.
mkmanifest() { mkdir -p "$repo/$1"; printf '{\n    "name": "%s",\n    "version": "%s",\n}\n' "$1" "$2" > "$repo/$1/__manifest__.py"; }
mkmanifest mod_keep   17.0.1.0.1
mkmanifest mod_nobump 17.0.2.0.0
mkmanifest mod_old    17.0.9.0.0
git -C "$repo" add -A; git -C "$repo" commit --quiet -m base
git -C "$repo" branch -M main
git -C "$repo" push --quiet -u origin main
git -C "$repo" checkout --quiet -b staging
shas=()
for i in 1 2 3 4 5; do
  echo "c$i" >> "$repo/f"
  # c1 carries the module churn: one added, one bumped, one deleted.
  if [ "$i" = 1 ]; then
    mkmanifest mod_new 17.0.1.0.0
    mkmanifest mod_keep 17.0.1.0.2
    rm -rf "$repo/mod_old"
  fi
  # c2 edits a module WITHOUT touching its version — the inert-deploy case.
  if [ "$i" = 2 ]; then
    mkdir -p "$repo/mod_nobump/views"; echo '<odoo/>' > "$repo/mod_nobump/views/v.xml"
  fi
  git -C "$repo" add -A; git -C "$repo" commit --quiet -m "c$i"
  shas+=("$(git -C "$repo" rev-parse HEAD)")
done
git -C "$repo" push --quiet -u origin staging

# One PR per commit, except c3+c4 which share PR 4 — a PR must appear once, not
# once per commit it contributed. PR 4 carries its id only on the branch (the
# weakest source); PR 5 carries one nowhere and must stay unresolved.
cat > "$fx/commit-${shas[0]}.jsonl" <<'J'
{"number":1,"url":"https://x/1","title":"feat(mod_a): thing [task 30412]","author":"a","head":"30412-thing","merged_at":"2026-01-01T00:00:00Z"}
J
cat > "$fx/commit-${shas[1]}.jsonl" <<'J'
{"number":2,"url":"https://x/2","title":"30455#human style | with a pipe","author":"b","head":"30455#human-style","merged_at":"2026-01-01T00:00:00Z"}
J
cat > "$fx/commit-${shas[2]}.jsonl" <<'J'
{"number":4,"url":"https://x/4","title":"Survey CSS Fix","author":"c","head":"30500-css-fix","merged_at":"2026-01-01T00:00:00Z"}
J
cp "$fx/commit-${shas[2]}.jsonl" "$fx/commit-${shas[3]}.jsonl"
# PR 99 is OPEN: its branch merely contains this sha. The commits/{sha}/pulls API
# really does return these, and unfiltered they inflate a release by every open
# branch cut from the source. It must be dropped.
cat > "$fx/commit-${shas[4]}.jsonl" <<'J'
{"number":5,"url":"https://x/5","title":"Portal SCSS folder","author":"d","head":"portal-scss","merged_at":"2026-01-01T00:00:00Z"}
{"number":99,"url":"https://x/99","title":"wip: not shipping [task 39999]","author":"e","head":"wip-branch","merged_at":null}
J
printf 'mod_a/models/x.py\nmod_a/__manifest__.py\n' > "$fx/files-1.txt"
printf 'mod_b/views/v.xml\nREADME.md\n.github/workflows/ci.yml\n' > "$fx/files-2.txt"
printf 'mod_c/static/src/scss/a.scss\n' > "$fx/files-4.txt"
printf 'mod_d/static/src/scss/b.scss\n' > "$fx/files-5.txt"

run() { PATH="$bin:$PATH" GH_FIXTURES="$fx" bash "$SUT" o/r "$repo" "$@" 2>/dev/null | tail -1; }

out="$(run staging main)"
expect "counts the commits"     "$(q "$out" 'm.commits')" "5"
expect "PRs are deduped"        "$(q "$out" 'm.prs.length')" "3"
expect "unresolved counted"     "$(q "$out" 'm.unresolved.length')" "1"

expect "tag id wins"            "$(q "$out" 'm.prs.find(p=>p.number===1).task_id')" "30412"
expect "tag source recorded"    "$(q "$out" 'm.prs.find(p=>p.number===1).task_id_source')" "tag"
expect "title prefix inferred"  "$(q "$out" 'm.prs.find(p=>p.number===2).task_id')" "30455"
expect "title source recorded"  "$(q "$out" 'm.prs.find(p=>p.number===2).task_id_source')" "title-prefix"
expect "inferred ids listed"    "$(q "$out" 'm.inferred_task_ids')" '["30455","30500"]'
# PR 4 carries no id in its title but its BRANCH does — the weakest source, and
# it must still be labelled inferred rather than trusted.
expect "branch id inferred"     "$(q "$out" 'm.prs.find(p=>p.number===4).task_id')" "30500"
expect "branch source recorded" "$(q "$out" 'm.prs.find(p=>p.number===4).task_id_source')" "branch"
# PR 5 has an id in neither place. Guessing one here is the failure this design
# exists to prevent.
expect "no id anywhere stays unresolved" "$(q "$out" 'm.unresolved[0].number')" "5"

# The regression that inflated a 14-commit release to 27 PRs: open PRs whose
# branch merely contains a shipping commit must never enter the manifest.
expect "open PR dropped"        "$(q "$out" 'm.prs.some(p=>p.number===99)?"kept":"dropped"')" "dropped"
expect "open PR task not listed" "$(q "$out" 'm.tasks.includes("39999")?"leaked":"clean"')" "clean"
expect "PR count <= commit count" "$(q "$out" '(m.prs.length+m.unresolved.length)<=m.commits?"sane":"inflated"')" "sane"

# ---------- module classification comes from the trees, not the PR files --------
expect "new module detected"     "$(q "$out" 'm.modules_new')" '["mod_new"]'
expect "removed module detected" "$(q "$out" 'm.modules_removed')" '["mod_old"]'
expect "updated modules listed"  "$(q "$out" 'm.modules_updated.map(x=>x.name)')" '["mod_keep","mod_nobump"]'
expect "version bump recorded"   "$(q "$out" 'm.modules_updated.find(x=>x.name==="mod_keep").bumped')" "true"
expect "old version is target"   "$(q "$out" 'm.modules_updated.find(x=>x.name==="mod_keep").version_from')" "17.0.1.0.1"
expect "new version is source"   "$(q "$out" 'm.modules_updated.find(x=>x.name==="mod_keep").version_to')" "17.0.1.0.2"
# Changed files, unchanged version: on odoo.sh this deploys and does nothing.
expect "unbumped module flagged" "$(q "$out" 'm.modules_updated.find(x=>x.name==="mod_nobump").bumped')" "false"
# A module added and removed inside the same delta is in neither tree, so it
# needs no command and must not appear anywhere.
expect "removed module gets no install" "$(q "$out" 'm.modules_new.includes("mod_old")?"wrong":"right"')" "right"

expect "modules from files"     "$(q "$out" 'm.prs.find(p=>p.number===1).modules')" '["mod_a"]'
# Repo-root files and dotted/hidden paths are not modules.
expect "non-module paths dropped" "$(q "$out" 'm.prs.find(p=>p.number===2).modules')" '["mod_b"]'
expect "module list is the union" "$(q "$out" 'm.modules')" '["mod_a","mod_b","mod_c","mod_d"]'

# A raw pipe in a title would split the cell and shift every column after it.
expect "pipes escaped in the table" "$(q "$out" 'm.table_md.includes("\\|") ? "escaped" : "raw"')" "escaped"
expect "inferred marked in table"   "$(q "$out" 'm.table_md.includes("30455 ?") ? "marked" : "plain"')" "marked"
expect "unresolved row present"     "$(q "$out" 'm.table_md.includes("_unresolved_") ? "yes" : "no"')" "yes"

# ---------- truncation is announced, never silent -------------------------------
out="$(run staging main --max-commits 2)"
expect "truncation reported" "$(q "$out" 'm.skipped_commits')" "3"

# ---------- guards ---------------------------------------------------------------
PATH="$bin:$PATH" GH_FIXTURES="$fx" bash "$SUT" o/r "$repo" main staging >/dev/null 2>&1
expect "empty delta is 5" "$?" "5"
PATH="$bin:$PATH" GH_FIXTURES="$fx" bash "$SUT" o/r "$repo" nosuch main >/dev/null 2>&1
expect "unknown branch is 2" "$?" "2"
PATH="$bin:$PATH" GH_FIXTURES="$fx" bash "$SUT" o/r "$work" staging main >/dev/null 2>&1
expect "non-repo path is 2" "$?" "2"

echo "{\"passed\": $pass, \"failed\": $fail}"
[ "$fail" -eq 0 ]
