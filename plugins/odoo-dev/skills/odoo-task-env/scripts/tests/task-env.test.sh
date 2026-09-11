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
# The best-effort tracking probes would reach a real odoo-sdk if one is on PATH,
# which breaks this suite's no-network promise; task-tracking.test.sh covers them
# with a stub. Pinned off here so these cases stay about worktree/stack behaviour.
export ODOO_TASK_TRACKING=0
out="$(cd / && bash "$SCRIPTS/worktree-ensure.sh" anyrepo 4242 my-slug UAT --repo-path "$repo" 2>&1 | tail -1)"
rc=$?
expect_exit "worktree with --repo-path succeeds" 0 $rc
expect "worktree branch" "$(field "$out" branch)" "4242-my-slug"
expect "worktree status created" "$(field "$out" status)" "created"
expect "worktree path" "$(field "$out" worktree)" "$repo/.worktrees/task-4242"
[ -d "$repo/.worktrees/task-4242" ]; expect_exit "worktree exists on disk" 0 $?
grep -qxF '.worktrees/' "$repo/.git/info/exclude"; expect_exit "worktrees excluded" 0 $?

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
PATH="$work/nodeps" "$work/nodeps/bash" "$SCRIPTS/preflight.sh" --soft >/dev/null 2>&1
expect_exit "--soft exits 0 anyway" 0 $?

echo "{\"passed\": $pass, \"failed\": $fail}"
[ "$fail" -eq 0 ]
