#!/usr/bin/env bash
# worktree-ensure.sh — idempotently create (or reuse) the task worktree.
#
# Usage: worktree-ensure.sh <repo> <task_id> <slug> <base_branch> [<resume_branch>] [--repo-path DIR]
#
# Effects (all idempotent):
#   - appends ".worktrees/" to the repo's .git/info/exclude (grep-before-append)
#   - creates <repo>/$WORKTREE_SUBDIR/task-<id> on branch "<id>-<slug>" from
#     origin/<base_branch>, or reuses the existing worktree/branch
#
# Resume: given a 5th argument, that EXACT ref is adopted instead of computing
# "<id>-<slug>" — a human branch (<id>#<slug>) is continued rather than restarted
# beside. This script deliberately does not glob for candidates: existing-work.sh
# found them and a human (or its single-candidate rule) chose one. Single
# responsibility, and no branch is ever guessed at from inside the lock.
#
# Session FSM: the odoo-mcp `start_task` tool ALSO creates a "<id>-<slug>" branch.
# When a tracking session was started that way, pass its branch as <resume_branch>
# so this script adopts it. Creating a second branch for one task is how a task
# ends up with two half-finished heads and no PR.
#
# Concurrency: every git operation below runs under an exclusive flock on
# <git-dir>/odoo-task-env-worktree.lock. Two callers working on the same repo at
# the same time otherwise race `git fetch`'s FETCH_HEAD.lock and the
# check-then-act around `worktree list` / `worktree add`; under `set -e` the
# loser dies with no retry. The lock is per repo, so different repos stay fully
# parallel, and it protects every caller of this script, not just one pipeline.
#
# Reuse: on the reuse path the branch is READ from the worktree, never
# recomputed from the arguments — a renamed Odoo task yields a new slug, and
# printing a branch that was never created hands the caller something it cannot
# push.
#
# Last stdout line: {"worktree": ..., "branch": ..., "status": "created"|"reused"}
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/_common.sh"

WORKTREE_SUBDIR="${WORKTREE_SUBDIR:-.worktrees}"

usage() {
  echo "usage: worktree-ensure.sh <repo> <task_id> <slug> <base_branch> [<resume_branch>] [--repo-path DIR]" >&2
  exit 2
}

positional=(); repo_path=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo-path) repo_path="${2:?}"; shift 2 ;;
    --*) echo "unknown option: $1" >&2; usage ;;
    *) positional+=("$1"); shift ;;
  esac
done
[ "${#positional[@]}" -ge 4 ] && [ "${#positional[@]}" -le 5 ] || usage
repo="${positional[0]}"; task_id="${positional[1]}"; slug="${positional[2]}"
default_branch="${positional[3]}"; resume_branch="${positional[4]:-}"

[ -n "$slug" ] || { echo "slug must not be empty" >&2; exit 2; }
[ -n "$default_branch" ] || { echo "default_branch must not be empty" >&2; exit 2; }

repo_dir="$(resolve_repo_dir "$repo" "$repo_path")" || exit 2

wt="$repo_dir/$WORKTREE_SUBDIR/task-$task_id"

git_dir="$(git -C "$repo_dir" rev-parse --git-dir)"
case "$git_dir" in /*) : ;; *) git_dir="$repo_dir/$git_dir" ;; esac

# Everything from here to the closing brace is serialized per repo. The lock file
# lives in the git dir, which is exactly the state being contended for.
lock_file="$git_dir/odoo-task-env-worktree.lock"
exec 9>"$lock_file"
flock 9

# Keep worktrees invisible to the repo (local state, not repo code).
mkdir -p "$git_dir/info"
touch "$git_dir/info/exclude"
grep -qxF "$WORKTREE_SUBDIR/" "$git_dir/info/exclude" || echo "$WORKTREE_SUBDIR/" >> "$git_dir/info/exclude"

if git -C "$repo_dir" worktree list --porcelain | grep -qxF "worktree $wt"; then
  # Report the branch the worktree is ACTUALLY on, not "$task_id-$slug".
  branch="$(git -C "$wt" rev-parse --abbrev-ref HEAD)"
  [ "$branch" != "HEAD" ] || { echo "worktree is in detached HEAD state: $wt" >&2; exit 2; }
  status="reused"
elif [ -n "$resume_branch" ]; then
  # Adopt the chosen branch as it stands. Never reset, rebase, or re-cut it: the
  # commits already on it are the work this run is continuing.
  branch="$resume_branch"
  [ -e "$wt" ] && { echo "path exists but is not a registered worktree: $wt" >&2; exit 2; }
  git -C "$repo_dir" fetch origin "$default_branch"
  if git -C "$repo_dir" show-ref --verify --quiet "refs/heads/$branch"; then
    git -C "$repo_dir" worktree add "$wt" "$branch"
  elif git -C "$repo_dir" show-ref --verify --quiet "refs/remotes/origin/$branch"; then
    git -C "$repo_dir" worktree add --track -b "$branch" "$wt" "origin/$branch"
  else
    echo "resume branch not found locally or on origin: $branch" >&2
    exit 2
  fi
  status="created"
else
  branch="$task_id-$slug"
  [ -e "$wt" ] && { echo "path exists but is not a registered worktree: $wt" >&2; exit 2; }
  git -C "$repo_dir" fetch origin "$default_branch"
  if git -C "$repo_dir" show-ref --verify --quiet "refs/heads/$branch"; then
    git -C "$repo_dir" worktree add "$wt" "$branch"
  else
    git -C "$repo_dir" worktree add "$wt" -b "$branch" "origin/$default_branch"
  fi
  status="created"
fi

flock -u 9
exec 9>&-

node -e '
  const [wt, branch, status] = process.argv.slice(1);
  console.log(JSON.stringify({ worktree: wt, branch, status }));
' "$wt" "$branch" "$status"
