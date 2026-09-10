#!/usr/bin/env bash
# existing-work.test.sh — behaviour test for existing-work.sh.
#
#   bash skills/odoo-task-env/scripts/tests/existing-work.test.sh
#
# Runs anywhere: no network, no docker, no repos tree, no real gh. It builds a
# throwaway repo under $TMPDIR with a local bare "origin", plants the branch shapes
# the client repos actually carry, stubs gh on PATH, and asserts one state per case.
# This is the one script change in the resume work that is testable outside the
# orchestrator container.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$SCRIPT_DIR/../existing-work.sh"
[ -x "$SUT" ] || { echo "not executable: $SUT" >&2; exit 1; }

work="$(mktemp -d "${TMPDIR:-/tmp}/existing-work-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

failures=0
checked=0

# ---------- assertions ----------
field() {
  node -e '
    const value = process.argv.slice(2).reduce(
      (node, key) => (node === null || node === undefined ? node : node[key]),
      JSON.parse(process.argv[1]),
    );
    console.log(value === null || value === undefined ? "null" : String(value));
  ' "$1" "${@:2}"
}

expect() {
  local label="$1" actual="$2" wanted="$3"
  checked=$((checked + 1))
  if [ "$actual" = "$wanted" ]; then
    echo "PASS $label"
  else
    echo "FAIL $label: wanted '$wanted', got '$actual'" >&2
    failures=$((failures + 1))
  fi
}

# ---------- fixture repo ----------
origin="$work/origin.git"
export REPOS_DIR="$work/repos"
repo="$REPOS_DIR/QOC"
mkdir -p "$REPOS_DIR"

mkdir -p "$work/no-hooks"
git init --quiet --bare "$origin"
git init --quiet "$repo"
git -C "$repo" symbolic-ref HEAD refs/heads/Odoov18
git -C "$repo" config user.email test@example.invalid
git -C "$repo" config user.name "existing-work test"
# The fixture must not inherit the machine's global hooks (commit-msg linters,
# secret scanners): they have opinions about a throwaway repo's commits.
git -C "$repo" config core.hooksPath "$work/no-hooks"
git -C "$repo" remote add origin "$origin"

commit_on() {
  local branch="$1" file="$2"
  git -C "$repo" checkout --quiet -B "$branch"
  echo "$file" > "$repo/$file"
  git -C "$repo" add "$file"
  git -C "$repo" commit --quiet -m "chore: $file"
}

commit_on Odoov18 base.txt
git -C "$repo" push --quiet -u origin Odoov18

# 29617 — two unmerged candidates, one per separator: nobody can pick for the human.
git -C "$repo" checkout --quiet -b '29617#printed-manufacturing-order' Odoov18
commit_on '29617#printed-manufacturing-order' hash-branch.txt
git -C "$repo" push --quiet origin '29617#printed-manufacturing-order'
git -C "$repo" checkout --quiet -b 29617-printed-manufacturing-order Odoov18
commit_on 29617-printed-manufacturing-order dash-branch.txt

# 29616 — pushed, then deleted locally: a remote-only candidate is still resumable.
git -C "$repo" checkout --quiet -b '29616#pr-branch' Odoov18
commit_on '29616#pr-branch' pr-branch.txt
git -C "$repo" push --quiet origin '29616#pr-branch'
git -C "$repo" checkout --quiet Odoov18
git -C "$repo" branch --quiet -D '29616#pr-branch'

# 27072 — merged into the default branch: complete, whatever its Odoo stage says.
git -C "$repo" checkout --quiet -b 27072-code-complete Odoov18
commit_on 27072-code-complete merged-work.txt
git -C "$repo" checkout --quiet Odoov18
git -C "$repo" merge --quiet --no-ff -m "merge 27072" 27072-code-complete
git -C "$repo" push --quiet origin Odoov18

# 31514 — a bare "<id>" branch, which the ([#-]|$) alternation exists to catch.
git -C "$repo" checkout --quiet -b 31514 Odoov18
commit_on 31514 bare-id.txt
git -C "$repo" checkout --quiet Odoov18

# ---------- gh stub ----------
stub_dir="$work/bin"
mkdir -p "$stub_dir"
cat > "$stub_dir/gh" <<'STUB'
#!/usr/bin/env bash
if [ "${GH_STUB_FAIL:-0}" = "1" ]; then
  echo "gh: could not authenticate (stub)" >&2
  exit 1
fi
cat "${GH_STUB_PRS:?GH_STUB_PRS must point at a json file}"
STUB
chmod +x "$stub_dir/gh"
export PATH="$stub_dir:$PATH"

echo '[]' > "$work/no-prs.json"
cat > "$work/open-pr.json" <<'JSON'
[{"number":17,"state":"OPEN","url":"https://example.invalid/pull/17","headRefName":"29616#pr-branch","isDraft":false,"mergedAt":null}]
JSON
cat > "$work/merged-pr.json" <<'JSON'
[{"number":9,"state":"MERGED","url":"https://example.invalid/pull/9","headRefName":"40001#branch-deleted-after-merge","isDraft":false,"mergedAt":"2026-07-01T10:00:00Z"}]
JSON

export GH_STUB_PRS="$work/no-prs.json"

run_sut() {
  "$SUT" "$@" 2>/dev/null | tail -1
}

# ---------- cases ----------
out="$(run_sut QOC 29617 Odoov18)"
expect "29617 two unmerged candidates is ambiguous" "$(field "$out" state)" "ambiguous"
expect "29617 names no branch to resume" "$(field "$out" branch)" "null"
expect "29617 lists both separators" "$(field "$out" candidates length)" "2"

out="$(run_sut QOC 29616 Odoov18)"
expect "29616 remote-only candidate resumes" "$(field "$out" state)" "resume"
expect "29616 resumes the pushed branch" "$(field "$out" branch)" "29616#pr-branch"
expect "29616 candidate is remote-only" "$(field "$out" candidates 0 has_local)" "false"
expect "29616 counts its commit" "$(field "$out" candidates 0 commits)" "1"

out="$(GH_STUB_PRS="$work/open-pr.json" run_sut QOC 29616 Odoov18)"
expect "an open PR joins its candidate" "$(field "$out" prs length)" "1"
expect "an open PR does not make it complete" "$(field "$out" state)" "resume"

out="$(run_sut QOC 27072 Odoov18)"
expect "27072 merged into the default branch is complete" "$(field "$out" state)" "complete"
expect "27072 merged candidate is flagged merged" "$(field "$out" candidates 0 merged)" "true"

out="$(run_sut QOC 31514 Odoov18)"
expect "a bare <id> branch is a candidate" "$(field "$out" state)" "resume"
expect "a bare <id> branch resumes by name" "$(field "$out" branch)" "31514"

out="$(run_sut QOC 30877 Odoov18)"
expect "no candidate is none" "$(field "$out" state)" "none"
expect "no candidate lists nothing" "$(field "$out" candidates length)" "0"

out="$(GH_STUB_PRS="$work/merged-pr.json" run_sut QOC 40001 Odoov18)"
expect "a MERGED PR whose branch is gone is still complete" "$(field "$out" state)" "complete"

out="$(GH_STUB_FAIL=1 run_sut QOC 29616 Odoov18)"
expect "a gh failure is reported, not fatal" "$(field "$out" state)" "resume"
expect "a gh failure empties the PR list" "$(field "$out" prs length)" "0"
if [ "$(field "$out" gh_error)" = "null" ]; then
  echo "FAIL a gh failure records gh_error" >&2
  failures=$((failures + 1))
else
  echo "PASS a gh failure records gh_error"
fi
checked=$((checked + 1))

echo "---"
echo "$checked checked, $failures failed"
[ "$failures" -eq 0 ]
