#!/usr/bin/env bash
# task-env.test.sh — offline behaviour tests for the parameterization this skill
# added on top of the lifted scripts: --repo-path (no repos tree at all),
# WORKTREE_SUBDIR, worktree create/reuse/resume, and the stack-ensure context
# split. No network, no docker, no gh, no Odoo.
#
# The lifted scripts' own behaviour is covered by existing-work.test.sh.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS="$(cd "$SCRIPT_DIR/.." && pwd)"
work="$(mktemp -d "${TMPDIR:-/tmp}/task-env-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
expect() { # expect <label> <actual> <wanted>
  if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted '$3', got '$2'" >&2; fi
}
expect_exit() { # expect_exit <label> <wanted> <actual>
  if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted exit $2, got $3" >&2; fi
}
field() { node -e 'console.log(String(JSON.parse(process.argv[1])[process.argv[2]] ?? "null"))' "$1" "$2"; }

# ---------- fixture: a repo with a local bare origin, no repos tree ------------
origin="$work/origin.git"; repo="$work/checkout"
mkdir -p "$work/no-hooks"
git init --quiet --bare "$origin"
git init --quiet "$repo"
git -C "$repo" config user.email test@example.invalid
git -C "$repo" config user.name "task-env test"
git -C "$repo" config core.hooksPath "$work/no-hooks"
git -C "$repo" remote add origin "$origin"
echo seed > "$repo/README"
git -C "$repo" add README
git -C "$repo" commit --quiet -m "seed"
git -C "$repo" branch -M UAT
git -C "$repo" push --quiet -u origin UAT

# ---------- --repo-path works with no repos tree resolvable -------------------
# REPOS_DIR deliberately empty AND the candidate list pointed at nothing: if the
# scripts still resolved a tree, --repo-path would not really be an escape hatch.
export REPOS_DIR="" REPOS_DIR_CANDIDATES="$work/nonexistent"
out="$(cd / && bash "$SCRIPTS/worktree-ensure.sh" anyrepo 4242 my-slug UAT --repo-path "$repo" 2>&1 | tail -1)"
rc=$?
expect_exit "worktree with --repo-path succeeds" 0 $rc
expect "worktree branch" "$(field "$out" branch)" "4242-my-slug"
expect "worktree status created" "$(field "$out" status)" "created"
expect "worktree path" "$(field "$out" worktree)" "$repo/.worktrees/task-4242"
[ -d "$repo/.worktrees/task-4242" ]; expect_exit "worktree exists on disk" 0 $?
grep -qxF '.worktrees/' "$repo/.git/info/exclude"; expect_exit "worktrees excluded" 0 $?
# A new task branch must have no upstream: git's default would track origin/UAT,
# and a bare "git push" would then target the base branch.
git -C "$repo/.worktrees/task-4242" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' >/dev/null 2>&1
expect_exit "new task branch has no upstream" 128 $?

# ---------- rerun reuses, and reads the branch off the worktree ---------------
# Renaming the branch simulates a renamed Odoo task: the slug argument is now
# stale, and recomputing from arguments would report a branch nobody can push.
git -C "$repo/.worktrees/task-4242" branch -m 4242-renamed-by-hand
out="$(bash "$SCRIPTS/worktree-ensure.sh" anyrepo 4242 my-slug UAT --repo-path "$repo" 2>&1 | tail -1)"
expect "rerun reuses" "$(field "$out" status)" "reused"
expect "rerun reports the ACTUAL branch" "$(field "$out" branch)" "4242-renamed-by-hand"

# ---------- resume adopts an existing branch, does not re-cut it --------------
git -C "$repo" branch 5150#human-style-branch UAT
out="$(bash "$SCRIPTS/worktree-ensure.sh" anyrepo 5150 auto-slug UAT '5150#human-style-branch' --repo-path "$repo" 2>&1 | tail -1)"
expect "resume adopts the given branch" "$(field "$out" branch)" '5150#human-style-branch'
expect "resume creates the worktree" "$(field "$out" status)" "created"

# A resume branch that exists only on origin is checked out tracking its own
# origin ref (not the base), so the upstream stays correct.
git -C "$repo" branch 5151-remote-only UAT
git -C "$repo" push --quiet origin 5151-remote-only
git -C "$repo" branch -D 5151-remote-only >/dev/null
out="$(bash "$SCRIPTS/worktree-ensure.sh" anyrepo 5151 auto-slug UAT 5151-remote-only --repo-path "$repo" 2>&1 | tail -1)"
expect "remote-only resume creates the worktree" "$(field "$out" status)" "created"
expect "remote-only resume tracks its own origin ref" \
  "$(git -C "$repo/.worktrees/task-5151" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>&1)" \
  "origin/5151-remote-only"

bash "$SCRIPTS/worktree-ensure.sh" anyrepo 6000 s UAT 'no-such-branch' --repo-path "$repo" >/dev/null 2>&1
expect_exit "missing resume branch is an error" 2 $?

# ---------- WORKTREE_SUBDIR is honoured ---------------------------------------
out="$(WORKTREE_SUBDIR=.trees bash "$SCRIPTS/worktree-ensure.sh" anyrepo 7000 s UAT --repo-path "$repo" 2>&1 | tail -1)"
expect "custom subdir path" "$(field "$out" worktree)" "$repo/.trees/task-7000"
grep -qxF '.trees/' "$repo/.git/info/exclude"; expect_exit "custom subdir excluded" 0 $?

# ---------- bad inputs ---------------------------------------------------------
bash "$SCRIPTS/worktree-ensure.sh" anyrepo 1 s UAT --repo-path "$work/not-a-repo" >/dev/null 2>&1
expect_exit "non-repo path rejected" 2 $?
bash "$SCRIPTS/worktree-ensure.sh" anyrepo 1 "" UAT --repo-path "$repo" >/dev/null 2>&1
expect_exit "empty slug rejected" 2 $?
bash "$SCRIPTS/existing-work.sh" anyrepo notanumber UAT --repo-path "$repo" >/dev/null 2>&1
expect_exit "non-numeric task id rejected" 2 $?
bash "$SCRIPTS/existing-work.sh" anyrepo 1 no-such-base --repo-path "$repo" >/dev/null 2>&1
expect_exit "unknown base branch rejected" 2 $?

# ---------- stack-ensure refuses worktree paths -------------------------------
bash "$SCRIPTS/stack-ensure.sh" "repo/.worktrees/task-1" >/dev/null 2>&1
expect_exit "stack-ensure refuses a worktree path" 2 $?
bash "$SCRIPTS/stack-ensure.sh" "a/b" >/dev/null 2>&1
expect_exit "stack-ensure refuses a path" 2 $?

# Context split: with no docker and no odoo on PATH it must say "wrong place"
# (exit 5), not die on `docker: command not found`.
# A PATH carrying bash and nothing else. /usr/bin has an odoo binary on this
# image, so leaving it on would satisfy the in-container branch and never reach
# the wrong-place path at all.
mkdir -p "$work/minbin"
for b in bash dirname; do ln -sf "$(command -v $b)" "$work/minbin/$b"; done
out="$(PATH="$work/minbin" "$work/minbin/bash" "$SCRIPTS/stack-ensure.sh" somerepo 2>&1)"
rc=$?
expect_exit "no docker and no odoo exits 5" 5 $rc
case "$out" in *"HOST"*) pass=$((pass+1)) ;; *) fail=$((fail+1)); echo "FAIL exit-5 message should name the host context: $out" >&2 ;; esac

# odoo on PATH but postgres not answering is not a working container: with no
# docker either there is nowhere to run, so exit 5 rather than a half-true "in-container".
mkdir -p "$work/pgfail"
for b in bash dirname grep head tail; do ln -sf "$(command -v $b)" "$work/pgfail/$b"; done
printf '#!/usr/bin/env bash\necho "Odoo Server 19.0-20260810"\n' > "$work/pgfail/odoo"
printf '#!/usr/bin/env bash\nexit 2\n' > "$work/pgfail/pg_isready"
chmod +x "$work/pgfail/odoo" "$work/pgfail/pg_isready"
out="$(PATH="$work/pgfail" "$work/pgfail/bash" "$SCRIPTS/stack-ensure.sh" somerepo 2>&1)"
rc=$?
expect_exit "odoo without a reachable postgres and no docker exits 5" 5 $rc

# In-container: the core series is REPORTED, and a stated target that disagrees
# with it is flagged rather than left for a green 0-test run to not mention (#978).
# A PATH holding only what that branch needs, with a stubbed 19.0 odoo on it.
mkdir -p "$work/inbin"
for b in bash dirname grep head tail; do ln -sf "$(command -v $b)" "$work/inbin/$b"; done
printf '#!/usr/bin/env bash\necho "Odoo Server 19.0-20260810"\n' > "$work/inbin/odoo"
printf '#!/usr/bin/env bash\nexit 0\n' > "$work/inbin/pg_isready"
chmod +x "$work/inbin/odoo" "$work/inbin/pg_isready"

out="$(PATH="$work/inbin" "$work/inbin/bash" "$SCRIPTS/stack-ensure.sh" somerepo 2>/dev/null | tail -1)"
expect "in-container still says so" "$(field "$out" status)" "in-container"
expect "in-container reports the full odoo --version line" "$(field "$out" odoo_version)" "Odoo Server 19.0-20260810"
expect "no target means no verdict" "$(field "$out" series_mismatch)" "null"

rc=0
out="$(PATH="$work/inbin" "$work/inbin/bash" "$SCRIPTS/stack-ensure.sh" somerepo --target-series 18.0 2>/dev/null | tail -1)" || rc=$?
expect_exit "a mismatch is a warning, not a failure" 0 $rc
expect "a 19.0 container flags an 18.0 target" "$(field "$out" series_mismatch)" "true"
for series in 18.0 19.0; do
  case "$(field "$out" warning)" in *"$series"*) pass=$((pass+1)) ;;
    *) fail=$((fail+1)); echo "FAIL the warning should name $series: $(field "$out" warning)" >&2 ;; esac
done

out="$(PATH="$work/inbin" "$work/inbin/bash" "$SCRIPTS/stack-ensure.sh" somerepo --target-series 19.0 2>/dev/null | tail -1)"
expect "the matching series is not flagged" "$(field "$out" series_mismatch)" "null"

# docker on PATH changes nothing (#1053): the devcontainer mounts the host socket,
# so odoo + pg_isready decide. The docker stub records any call, because a call
# would mean the host path ran.
mkdir -p "$work/dockbin"
for b in bash dirname grep head tail; do ln -sf "$(command -v $b)" "$work/dockbin/$b"; done
cp "$work/inbin/odoo" "$work/inbin/pg_isready" "$work/dockbin/"
printf '#!/usr/bin/env bash\necho "$*" >> "%s/docker.calls"\nexit 1\n' "$work" > "$work/dockbin/docker"
chmod +x "$work/dockbin/docker"
rc=0
out="$(PATH="$work/dockbin" "$work/dockbin/bash" "$SCRIPTS/stack-ensure.sh" somerepo 2>/dev/null)" || rc=$?
expect_exit "docker plus odoo plus postgres exits 0" 0 $rc
expect "docker on PATH still reports in-container" "$(field "$out" status)" "in-container"
[ ! -e "$work/docker.calls" ]; expect_exit "the host path never called docker" 0 $?

# --repo-path is accepted in container mode, in both spellings, and ignored.
out="$(PATH="$work/inbin" "$work/inbin/bash" "$SCRIPTS/stack-ensure.sh" somerepo --repo-path "$work/nonexistent" 2>/dev/null)"
expect "--repo-path DIR is accepted in a container" "$(field "$out" status)" "in-container"
out="$(PATH="$work/inbin" "$work/inbin/bash" "$SCRIPTS/stack-ensure.sh" --repo-path="$work/nonexistent" somerepo 2>/dev/null)"
expect "--repo-path=DIR is accepted in a container" "$(field "$out" status)" "in-container"
PATH="$work/inbin" "$work/inbin/bash" "$SCRIPTS/stack-ensure.sh" somerepo --repo-path >/dev/null 2>&1
expect_exit "--repo-path without a value is a usage error" 2 $?

# side_cores: every ODOO_SRC_ROOT/odooNN-bin that is executable AND has odooNN.conf
# beside it; [] otherwise. odoo17-bin has no conf and odoo16-bin is not executable.
src="$work/src"; mkdir -p "$src"
out="$(ODOO_SRC_ROOT="$src" PATH="$work/inbin" "$work/inbin/bash" "$SCRIPTS/stack-ensure.sh" somerepo 2>/dev/null)"
expect "no side cores means an empty list" "$(node -e 'console.log(JSON.stringify(JSON.parse(process.argv[1]).side_cores))' "$out")" "[]"
printf '#!/usr/bin/env bash\necho "Odoo Server 18.0-20260101"\n' > "$src/odoo18-bin"
printf '#!/usr/bin/env bash\necho "Odoo Server 17.0-20260101"\n' > "$src/odoo17-bin"
printf '#!/usr/bin/env bash\necho "Odoo Server 16.0-20260101"\n' > "$src/odoo16-bin"
chmod +x "$src/odoo18-bin" "$src/odoo17-bin"
: > "$src/odoo18.conf"; : > "$src/odoo16.conf"
out="$(ODOO_SRC_ROOT="$src" PATH="$work/inbin" "$work/inbin/bash" "$SCRIPTS/stack-ensure.sh" somerepo 2>/dev/null)"
expect "only a bin with a conf beside it is a side core" "$(node -e 'console.log(JSON.stringify(JSON.parse(process.argv[1]).side_cores))' "$out")" \
  "[{\"series\":\"18.0\",\"bin\":\"$src/odoo18-bin\",\"conf\":\"$src/odoo18.conf\",\"version\":\"Odoo Server 18.0-20260101\"}]"
expect "the core is still the container's own" "$(field "$out" odoo_version)" "Odoo Server 19.0-20260810"

# Host mode (docker, no working odoo) with a repos tree that cannot be resolved:
# an explicit exit 2 that names the fix, never a bare set -e death. repos-dir.sh is
# replaced by a stub that fails as the real one does when no candidate qualifies.
mkdir -p "$work/hostbin" "$work/fakemap"
for b in bash dirname; do ln -sf "$(command -v $b)" "$work/hostbin/$b"; done
cp "$work/dockbin/docker" "$work/hostbin/docker"
printf '#!/usr/bin/env bash\necho "could not resolve the repos tree" >&2\nexit 1\n' > "$work/fakemap/repos-dir.sh"
chmod +x "$work/fakemap/repos-dir.sh"
out="$(ODOO_REPO_MAP_SCRIPTS="$work/fakemap" REPOS_DIR="" PATH="$work/hostbin" "$work/hostbin/bash" "$SCRIPTS/stack-ensure.sh" somerepo 2>&1)"
rc=$?
expect_exit "host mode with no repos tree exits 2" 2 $rc
for word in --repo-path REPOS_DIR; do
  case "$out" in *"$word"*) pass=$((pass+1)) ;; *) fail=$((fail+1)); echo "FAIL the repos-tree error should name $word: $out" >&2 ;; esac
done
# --repo-path makes the repo itself resolvable; a path that is not a directory is exit 2 too.
out="$(ODOO_REPO_MAP_SCRIPTS="$work/fakemap" REPOS_DIR="" PATH="$work/hostbin" "$work/hostbin/bash" "$SCRIPTS/stack-ensure.sh" somerepo --repo-path "$work/nonexistent" 2>&1)"
rc=$?
expect_exit "host mode with a missing --repo-path exits 2" 2 $rc
case "$out" in *"repo not found"*) pass=$((pass+1)) ;; *) fail=$((fail+1)); echo "FAIL a missing --repo-path should say 'repo not found': $out" >&2 ;; esac
[ ! -e "$work/docker.calls" ]; expect_exit "the host failures happened before any docker call" 0 $?

# ---------- preflight -----------------------------------------------------------
# Context detection is the whole point: the lifted original only knew the host
# case and reported four confident failures inside a perfectly good container.
out="$(bash "$SCRIPTS/preflight.sh" --soft 2>/dev/null | tail -1)"
ctx="$(field "$out" context)"
case "$ctx" in host|devcontainer) pass=$((pass+1)) ;;
  *) fail=$((fail+1)); echo "FAIL preflight context should be host or devcontainer, got '$ctx'" >&2 ;; esac
node -e 'const j=JSON.parse(process.argv[1]);if(!Array.isArray(j.failures)||!Array.isArray(j.warnings))process.exit(1)' "$out"
expect_exit "preflight failures/warnings are arrays" 0 $?

# A PATH carrying what preflight itself needs and none of what it checks FOR, so
# every check fails while the script still runs and still emits its JSON. (minbin
# is too bare: without node there is no JSON to assert on at all.)
mkdir -p "$work/nodeps"
for b in bash dirname node free awk ls wc; do ln -sf "$(command -v $b)" "$work/nodeps/$b"; done

# --soft reports the failures and still exits 0; without it the same run exits 1.
out="$(PATH="$work/nodeps" "$work/nodeps/bash" "$SCRIPTS/preflight.sh" --soft 2>/dev/null | tail -1)"
expect "everything missing is not ok" "$(field "$out" ok)" "false"
# No docker on that PATH, so it must take the container branch, not report four
# host failures.
expect "no docker means container context" "$(field "$out" context)" "devcontainer"
node -e 'const f=JSON.parse(process.argv[1]).failures;process.exit(f.includes("gh-auth")&&f.includes("odoo")?0:1)' "$out"
expect_exit "names the checks that failed" 0 $?
PATH="$work/nodeps" "$work/nodeps/bash" "$SCRIPTS/preflight.sh" >/dev/null 2>&1
expect_exit "hard mode exits 1 on failure" 1 $?

# docker on PATH next to a working odoo + postgres is still a devcontainer (#1053).
mkdir -p "$work/pfdock"
for b in bash dirname node free awk ls wc; do ln -sf "$(command -v $b)" "$work/pfdock/$b"; done
cp "$work/dockbin/odoo" "$work/dockbin/pg_isready" "$work/dockbin/docker" "$work/pfdock/"
out="$(PATH="$work/pfdock" "$work/pfdock/bash" "$SCRIPTS/preflight.sh" --soft 2>/dev/null | tail -1)"
expect "docker plus odoo plus postgres is a devcontainer" "$(field "$out" context)" "devcontainer"
[ ! -e "$work/docker.calls" ]; expect_exit "preflight ran no docker check in a container" 0 $?
# docker alone (no working odoo) is still the host.
out="$(PATH="$work/hostbin:$work/nodeps" "$work/nodeps/bash" "$SCRIPTS/preflight.sh" --soft 2>/dev/null | tail -1)"
expect "docker without odoo is the host" "$(field "$out" context)" "host"
PATH="$work/nodeps" "$work/nodeps/bash" "$SCRIPTS/preflight.sh" --soft >/dev/null 2>&1
expect_exit "--soft exits 0 anyway" 0 $?

# --target-series compares ODOO_VERSION in the container branch only.
out="$(ODOO_VERSION=19.0 PATH="$work/nodeps" "$work/nodeps/bash" "$SCRIPTS/preflight.sh" --soft --target-series 19.0 2>/dev/null | tail -1)"
node -e 'const f=JSON.parse(process.argv[1]).failures;process.exit(f.some(x=>x.startsWith("series:"))?1:0)' "$out"
expect_exit "a matching target series raises no series failure" 0 $?
out="$(ODOO_VERSION=17.0 PATH="$work/nodeps" "$work/nodeps/bash" "$SCRIPTS/preflight.sh" --soft --target-series 19.0 2>/dev/null | tail -1)"
node -e 'const f=JSON.parse(process.argv[1]).failures;process.exit(f.some(x=>x.startsWith("series:")&&x.includes("17.0")&&x.includes("19.0"))?0:1)' "$out"
expect_exit "a mismatched target series names both values" 0 $?
ODOO_VERSION=17.0 PATH="$work/nodeps" "$work/nodeps/bash" "$SCRIPTS/preflight.sh" --target-series 19.0 >/dev/null 2>&1
expect_exit "a mismatched target series exits 1 without --soft" 1 $?

# --custom-tree imports the enterprise closure. The python3 stub answers the
# odoo.__file__ probe, fails the import of account_accountant and passes the rest;
# a bare "-" run is the manifest parser and goes to the real python3.
mkdir -p "$work/pfcl"
for b in bash dirname node free awk ls wc tail; do ln -sf "$(command -v $b)" "$work/pfcl/$b"; done
cp "$work/dockbin/odoo" "$work/dockbin/pg_isready" "$work/pfcl/"
printf '#!/usr/bin/env bash\n[ "$1" = - ] && exec "%s" "$@"\ncase "$*" in\n  *odoo.__file__*) echo "%s/core/odoo/__init__.py" ;;\n  *odoo.addons.account_accountant*) echo "ImportError: cannot import name x" >&2; exit 1 ;;\n  *) exit 0 ;;\nesac\n' "$(command -v python3)" "$work" > "$work/pfcl/python3"
chmod +x "$work/pfcl/python3"
mkdir -p "$work/ent/19.0/account_accountant" "$work/ent/19.0/web_studio" "$work/core/odoo/addons/account" "$work/tree/my_mod" "$work/tree2/my_ok"
printf "{'name': 'a', 'depends': ['account']}\n" > "$work/ent/19.0/account_accountant/__manifest__.py"
printf "{'name': 'w', 'depends': ['account']}\n" > "$work/ent/19.0/web_studio/__manifest__.py"
printf "{'name': 'c', 'depends': []}\n" > "$work/core/odoo/addons/account/__manifest__.py"
printf "{'name': 'm', 'depends': ['account_accountant', 'nowhere']}\n" > "$work/tree/my_mod/__manifest__.py"
printf "{'name': 'm', 'depends': ['web_studio']}\n" > "$work/tree2/my_ok/__manifest__.py"
out="$(ODOO_ENTERPRISE_ROOT="$work/ent" PATH="$work/pfcl" "$work/pfcl/bash" "$SCRIPTS/preflight.sh" --soft --target-series 19.0 --custom-tree "$work/tree" 2>/dev/null | tail -1)"
node -e 'const f=JSON.parse(process.argv[1]).failures;process.exit(f.some(x=>x.startsWith("import account_accountant: ImportError"))?0:1)' "$out"
expect_exit "a failing enterprise import is named in failures" 0 $?
node -e 'const f=JSON.parse(process.argv[1]).failures;process.exit(f.includes("closure: unknown module nowhere")?0:1)' "$out"
expect_exit "an unknown dependency is named in failures" 0 $?
out="$(ODOO_ENTERPRISE_ROOT="$work/ent" PATH="$work/pfcl" "$work/pfcl/bash" "$SCRIPTS/preflight.sh" --soft --target-series 19.0 --custom-tree "$work/tree2" 2>/dev/null | tail -1)"
node -e 'const f=JSON.parse(process.argv[1]).failures;process.exit(f.some(x=>x.startsWith("import ")||x.startsWith("closure:"))?1:0)' "$out"
expect_exit "an importable enterprise closure raises no failure" 0 $?

# The state-dir check resolves through state-dir.sh: with ODOO_DEV_HOME unset, the
# directory $HOME/.config/odoo-dev satisfies it. The map file inside is not needed.
mkdir -p "$work/pfhome/.config/odoo-dev"
out="$(env -u ODOO_DEV_HOME HOME="$work/pfhome" PATH="$work/nodeps" "$work/nodeps/bash" "$SCRIPTS/preflight.sh" --soft 2>/dev/null | tail -1)"
node -e 'const f=JSON.parse(process.argv[1]).failures;process.exit(f.includes("state-dir")?1:0)' "$out"
expect_exit "state-dir check passes through the host default" 0 $?
out="$(env -u ODOO_DEV_HOME HOME="$work/pfempty" PATH="$work/nodeps" "$work/nodeps/bash" "$SCRIPTS/preflight.sh" --soft 2>/dev/null | tail -1)"
node -e 'const f=JSON.parse(process.argv[1]).failures;process.exit(f.includes("state-dir")?0:1)' "$out"
expect_exit "state-dir check fails when the default dir is missing" 0 $?
out="$(env ODOO_DEV_HOME="$work/pfelsewhere" PATH="$work/nodeps" "$work/nodeps/bash" "$SCRIPTS/preflight.sh" --soft 2>/dev/null | tail -1)"
node -e 'const f=JSON.parse(process.argv[1]).failures;process.exit(f.includes("state-dir")?0:1)' "$out"
expect_exit "state-dir check follows ODOO_DEV_HOME" 0 $?

# The odoo-dev CLI check compares the major version against 9.
mkdir -p "$work/pfcli"
for b in bash dirname node free awk ls wc; do ln -sf "$(command -v $b)" "$work/pfcli/$b"; done
for v in 9.0.1 0.1.0; do
  printf '#!/bin/sh\necho "odoo-dev %s"\n' "$v" > "$work/pfcli/odoo-dev"; chmod +x "$work/pfcli/odoo-dev"
  out="$(PATH="$work/pfcli" "$work/pfcli/bash" "$SCRIPTS/preflight.sh" --soft 2>/dev/null | tail -1)"
  got="$(node -e 'console.log(JSON.parse(process.argv[1]).failures.includes("odoo-dev-cli"))' "$out")"
  case "$v" in 9*) want=false ;; *) want=true ;; esac
  expect "odoo-dev $v: cli check fails" "$got" "$want"
done
out="$(PATH="$work/nodeps" "$work/nodeps/bash" "$SCRIPTS/preflight.sh" --soft 2>/dev/null | tail -1)"
node -e 'const f=JSON.parse(process.argv[1]).failures;process.exit(f.includes("odoo-dev-cli")?0:1)' "$out"
expect_exit "a missing odoo-dev is named in failures" 0 $?

echo "{\"passed\": $pass, \"failed\": $fail}"
[ "$fail" -eq 0 ]
