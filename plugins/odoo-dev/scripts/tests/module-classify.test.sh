#!/usr/bin/env bash
# module-classify.test.sh — offline tests for module-classify.sh.
#
# Real throwaway git repositories with real commits. No gh, no network, no Odoo.
#
# What is under test is the sorting rule itself, because that is where a wrong
# answer costs something: a module put in the update list never gets installed,
# and a module put in the install list forces a needless full update against a
# live database. The interesting rows are the ones a diff letter would get wrong
# — a manifest that was modified rather than added is an update, and a deleted
# module belongs to neither list.
#
# The last block runs one shared fixture through both this classifier and
# release-manifest.sh and asserts they agree, because the whole point of one
# shared script is that a task PR and the release that ships it cannot disagree
# about what installs.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SUT="$ROOT/scripts/module-classify.sh"
RELEASE_MANIFEST="$ROOT/skills/odoo-release/scripts/release-manifest.sh"
work="$(mktemp -d "${TMPDIR:-/tmp}/module-classify-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
expect() { if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted '$3', got '$2'" >&2; fi; }
q() { node -e '
  const j = JSON.parse(process.argv[1]);
  const v = new Function("m", "return " + process.argv[2])(j);
  console.log(typeof v === "object" ? JSON.stringify(v) : String(v));
' "$1" "$2"; }

# ---------- throwaway repo -------------------------------------------------------
repo="$work/repo"; mkdir -p "$work/no-hooks"
origin="$work/origin.git"; git init --quiet --bare "$origin"
git init --quiet "$repo"
git -C "$repo" config user.email t@e.invalid
git -C "$repo" config user.name t
git -C "$repo" config core.hooksPath "$work/no-hooks"
git -C "$repo" remote add origin "$origin"

mkmanifest() { mkdir -p "$repo/$1"; printf '{\n    "name": "%s",\n    "version": "%s",\n}\n' "$1" "$2" > "$repo/$1/__manifest__.py"; }
mkfile()     { mkdir -p "$(dirname "$repo/$1")"; printf '%s\n' "$2" > "$repo/$1"; }
commit()     { git -C "$repo" add -A; git -C "$repo" commit --quiet -m "$1"; }
branch()     { git -C "$repo" checkout --quiet -b "$1" main; }
publish()    { git -C "$repo" push --quiet -u origin "$1"; }

# Base: two modules that will be changed, one that will be deleted, plus paths
# that are NOT modules and must never appear in any list.
mkmanifest mod_existing      17.0.1.0.0
mkfile     mod_existing/models/x.py "X = 1"
mkmanifest mod_manifest_edit 17.0.2.0.0
mkfile     mod_manifest_edit/models/y.py "Y = 1"
mkmanifest mod_gone          17.0.9.0.0
mkfile     not_a_module/notes.txt "no manifest here"
mkfile     .github/workflows/ci.yml "on: push"
mkfile     README.md "root file"
commit base
git -C "$repo" branch -M main
publish main

# One branch per rule so a failure names the rule it broke.
branch feat-new
mkmanifest mod_new 17.0.1.0.0
mkfile     mod_new/models/z.py "Z = 1"
commit "add mod_new"; publish feat-new

branch feat-change
mkfile mod_existing/models/x.py "X = 2"
commit "edit mod_existing, manifest untouched"; publish feat-change

branch feat-manifest
mkmanifest mod_manifest_edit 17.0.2.0.1
commit "bump mod_manifest_edit manifest only"; publish feat-manifest

branch feat-mixed
mkmanifest mod_new 17.0.1.0.0
mkfile     mod_new/models/z.py "Z = 1"
mkfile     mod_existing/models/x.py "X = 3"
mkfile     not_a_module/notes.txt "still no manifest"
mkfile     .github/workflows/ci.yml "on: pull_request"
commit "one new module, one changed module"; publish feat-mixed

branch feat-del
rm -rf "$repo/mod_gone"
commit "delete mod_gone"; publish feat-del

# Everything at once — the fixture the drift check shares with release-manifest.
branch feat-all
mkmanifest mod_new   17.0.1.0.0
mkmanifest mod_alpha 17.0.1.0.0
mkfile     mod_existing/models/x.py "X = 4"
mkmanifest mod_manifest_edit 17.0.2.0.1
rm -rf "$repo/mod_gone"
commit "add two, change two, delete one"; publish feat-all

# main moves on AFTER the branches were cut. With a two-dot diff mod_late would
# look like a module the branches deleted; three dots start at the merge base and
# never see it.
git -C "$repo" checkout --quiet main
mkmanifest mod_late 17.0.1.0.0
commit "add mod_late on main"; publish main

run() { bash "$SUT" "$repo" "$@" 2>/dev/null | tail -1; }

# ---------- 1. a wholly new module installs, it does not update -------------------
out="$(run main feat-new)"
expect "new module installs"        "$(q "$out" 'm.install')" '["mod_new"]'
expect "new module is not an update" "$(q "$out" 'm.update')" '[]'
expect "new module removes nothing"  "$(q "$out" 'm.removed')" '[]'
expect "new module has no base version" "$(q "$out" 'm.details[0].version_from')" "null"
expect "new module reports its version" "$(q "$out" 'm.details[0].version_to')" "17.0.1.0.0"
# The branch predates mod_late, so a base-only module must not read as removed.
expect "base-only module is not removed" "$(q "$out" 'm.removed.includes("mod_late")?"wrong":"right"')" "right"

# ---------- 2. a changed module updates ------------------------------------------
out="$(run main feat-change)"
expect "changed module updates"      "$(q "$out" 'm.update')" '["mod_existing"]'
expect "changed module not installed" "$(q "$out" 'm.install')" '[]'
# Files changed, version untouched: on odoo.sh this deploys and does nothing.
expect "unbumped module flagged"     "$(q "$out" 'm.details[0].bumped')" "false"

# ---------- 3. manifest MODIFIED but not added is still an update -----------------
# The row a diff letter gets wrong: __manifest__.py shows as changed, and reading
# that as "new manifest" would order an install of a module already installed.
out="$(run main feat-manifest)"
expect "modified manifest updates"    "$(q "$out" 'm.update')" '["mod_manifest_edit"]'
expect "modified manifest not install" "$(q "$out" 'm.install')" '[]'
expect "old version from base"        "$(q "$out" 'm.details[0].version_from')" "17.0.2.0.0"
expect "new version from head"        "$(q "$out" 'm.details[0].version_to')" "17.0.2.0.1"
expect "bump recorded"                "$(q "$out" 'm.details[0].bumped')" "true"

# ---------- 4. a mixed PR sorts each module into its own list ---------------------
out="$(run main feat-mixed)"
expect "mixed PR installs the new one" "$(q "$out" 'm.install')" '["mod_new"]'
expect "mixed PR updates the old one"  "$(q "$out" 'm.update')" '["mod_existing"]'
# A directory without a manifest is not a module, whatever else it contains.
expect "manifest-less dir dropped"     "$(q "$out" 'JSON.stringify(m).includes("not_a_module")?"leaked":"clean"')" "clean"
expect "dotted top level dropped"      "$(q "$out" 'JSON.stringify(m).includes(".github")?"leaked":"clean"')" "clean"

# ---------- 5. a deleted module lands in neither list -----------------------------
# Deleting a directory removes code and uninstalls nothing, so there is no command
# to generate and it must not be smuggled into either list.
out="$(run main feat-del)"
expect "deleted module removed"        "$(q "$out" 'm.removed')" '["mod_gone"]'
expect "deleted module not installed"  "$(q "$out" 'm.install.includes("mod_gone")?"wrong":"right"')" "right"
expect "deleted module not updated"    "$(q "$out" 'm.update.includes("mod_gone")?"wrong":"right"')" "right"

# ---------- lists are sorted, and raw shas resolve ---------------------------------
out="$(run main feat-all)"
expect "install sorted"  "$(q "$out" 'm.install')" '["mod_alpha","mod_new"]'
expect "update sorted"   "$(q "$out" 'm.update')" '["mod_existing","mod_manifest_edit"]'
expect "removed listed"  "$(q "$out" 'm.removed')" '["mod_gone"]'

base_sha="$(git -C "$repo" rev-parse origin/main)"
head_sha="$(git -C "$repo" rev-parse origin/feat-all)"
out="$(run "$base_sha" "$head_sha")"
expect "raw shas resolve" "$(q "$out" 'm.install')" '["mod_alpha","mod_new"]'

# ---------- guards ------------------------------------------------------------------
bash "$SUT" "$repo" main >/dev/null 2>&1
expect "wrong arity is 2"   "$?" "2"
bash "$SUT" "$work" main feat-all >/dev/null 2>&1
expect "non-repo path is 2" "$?" "2"
bash "$SUT" "$repo" nosuch feat-all >/dev/null 2>&1
expect "unknown ref is 2"   "$?" "2"

# ---------- no drift between the PR answer and the release answer --------------------
# release-manifest.sh delegates here. If it ever stops delegating, a PR could say
# "-i mod_alpha" while the release that ships it says "-u mod_alpha", and one of
# the two would be run against a client database.
bin="$work/bin"; mkdir -p "$bin"
cat > "$bin/gh" <<'STUB'
#!/usr/bin/env bash
# The drift check is about modules, which come from the trees. No PR is needed.
exit 0
STUB
chmod +x "$bin/gh"

cls="$(run main feat-all)"
rel="$(PATH="$bin:$PATH" bash "$RELEASE_MANIFEST" o/r "$repo" feat-all main 2>/dev/null | tail -1)"
expect "release manifest ran"     "$(q "$rel" 'Array.isArray(m.modules_new)')" "true"
expect "install matches new"      "$(q "$cls" 'm.install')"  "$(q "$rel" 'm.modules_new')"
expect "update matches updated"   "$(q "$cls" 'm.update')"   "$(q "$rel" 'm.modules_updated.map(x=>x.name)')"
expect "removed matches removed"  "$(q "$cls" 'm.removed')"  "$(q "$rel" 'm.modules_removed')"

echo "module-classify.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
