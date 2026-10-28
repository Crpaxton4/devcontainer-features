#!/usr/bin/env bash
# existing-work.sh — discover prior work for a task, deterministically.
#
# Usage: existing-work.sh <repo> <task_id> <base_branch> [--repo-root DIR]
#
# Why: the pipeline used to probe exactly one ref, refs/heads/<id>-<slug> — the only
# name the automation itself ever creates. Humans push <id>#<slug>, so five commits
# and an open PR were invisible to the machine and a finished task was triaged as
# new work. This script looks for BOTH separators, across local and remote refs, and
# reports a STATE. It judges nothing: every field below is computed.
#
# Read-only. One `git fetch origin` to refresh remote-tracking refs, then queries
# only — no ref is created, moved, or deleted, and no worktree is touched.
#
# gh is best-effort: a failure degrades to "prs": [] plus a "gh_error" string and is
# never fatal (preflight.sh already gates `gh auth status`). PRs are matched on
# headRefName by the same <id>[#-] pattern rather than by an existing candidate ref,
# so a MERGED pull request whose branch was deleted afterwards still proves the work
# landed — which is the whole point of asking GitHub as well as git.
#
# Last stdout line: {"state", "branch", "candidates": [...], "prs": [...],
#                    "gh_error"}
#   complete   a candidate is merged into <base_branch>, or a PR is MERGED
#   resume     exactly one unmerged candidate — continue on "branch"
#   ambiguous  two or more unmerged candidates — a human picks
#   none       no candidate — start fresh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/_common.sh"

[ $# -ge 3 ] || { echo "usage: existing-work.sh <repo> <task_id> <base_branch> [--repo-root DIR]" >&2; exit 2; }
repo="$1"; task_id="$2"; base_branch="$3"; shift 3
repo_root=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo-root) repo_root="${2:?}"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

# Numeric by contract, and the check is load-bearing twice over: the id is
# interpolated into a grep -E pattern here and into a RegExp below.
case "$task_id" in
  ''|*[!0-9]*) echo "task_id must be numeric: $task_id" >&2; exit 2 ;;
esac

repo_dir="$(resolve_repo_dir "$repo" "$repo_root")" || exit 2

# Refreshes remote-tracking refs only. A repo with no reachable origin still reports
# on what is local, because an offline answer beats no answer at the triage gate.
git -C "$repo_dir" fetch --quiet origin 2>/dev/null \
  || echo "WARN could not fetch origin — reporting on local refs only" >&2

# An empty base_branch is what project-resolve.sh prints when no branch of the flow
# exists on origin: warn and measure against origin/HEAD.
base_ref=""
if [ -z "$base_branch" ]; then
  echo "WARN base_branch is empty (no branch of the flow exists on origin): using origin/HEAD" >&2
  git -C "$repo_dir" show-ref --verify --quiet refs/remotes/origin/HEAD && base_ref="refs/remotes/origin/HEAD"
else
  for candidate_base in "refs/remotes/origin/$base_branch" "refs/heads/$base_branch"; do
    if git -C "$repo_dir" show-ref --verify --quiet "$candidate_base"; then
      base_ref="$candidate_base"
      break
    fi
  done
fi
[ -n "$base_ref" ] || {
  echo "base branch '${base_branch:-origin/HEAD}' exists neither on origin nor locally in $repo_dir" >&2
  exit 2
}

# Both separators on purpose: '#' is what humans push, '-' is what this pipeline
# creates, and a bare '<id>' branch happens too.
ref_pattern="^(refs/heads/|refs/remotes/origin/)${task_id}([#-]|\$)"
mapfile -t refs < <(
  git -C "$repo_dir" for-each-ref --format='%(refname)' refs/heads refs/remotes/origin \
    | grep -E "$ref_pattern" || true
)

# One candidate per BRANCH NAME, not per ref: a branch that exists locally and on
# origin is one piece of work, and counting it twice would make every pushed branch
# look ambiguous. The local ref wins, because the local head is what a human would
# continue from.
names=()
declare -A chosen_ref=()
declare -A has_local=()
for ref in "${refs[@]}"; do
  case "$ref" in
    refs/heads/*) name="${ref#refs/heads/}" ;;
    *) name="${ref#refs/remotes/origin/}" ;;
  esac
  if [ -z "${chosen_ref[$name]:-}" ]; then
    names+=("$name")
    chosen_ref["$name"]="$ref"
    has_local["$name"]=false
  fi
  case "$ref" in
    refs/heads/*)
      chosen_ref["$name"]="$ref"
      has_local["$name"]=true
      ;;
  esac
done

candidates_tsv=""
if [ "${#names[@]}" -gt 0 ]; then
  for name in "${names[@]}"; do
    tip="$(git -C "$repo_dir" rev-parse "${chosen_ref[$name]}")"
    last_date="$(git -C "$repo_dir" log -1 --format=%cI "$tip")"
    merged=false
    if git -C "$repo_dir" merge-base --is-ancestor "$tip" "$base_ref"; then
      merged=true
    fi
    commits="$(git -C "$repo_dir" rev-list --count "$base_ref..$tip")"
    row="$(printf '%s\t%s\t%s\t%s\t%s\t%s' \
      "$name" "$tip" "$last_date" "$merged" "$commits" "${has_local[$name]}")"
    candidates_tsv="${candidates_tsv}${row}"$'\n'
  done
fi

gh_error=""
prs_json="[]"
origin_url="$(git -C "$repo_dir" remote get-url origin 2>/dev/null || true)"
slug="$(remote_slug "$origin_url")"
if ! command -v gh >/dev/null 2>&1; then
  gh_error="gh is not installed — pull request state unknown"
elif [ -z "$slug" ]; then
  gh_error="no origin remote — pull request state unknown"
else
  gh_stderr="$(mktemp "${TMPDIR:-/tmp}/existing-work-gh.XXXXXX")"
  if ! prs_json="$(gh pr list --repo "$slug" --state all --limit 100 \
      --json number,state,url,headRefName,isDraft,mergedAt 2>"$gh_stderr")"; then
    gh_error="$(head -c 400 "$gh_stderr")"
    prs_json="[]"
  fi
  rm -f "$gh_stderr"
fi

# json-get.sh binds name=value operands to `a`; the JSON on stdin is unused here.
"$SCRIPT_DIR/../../../scripts/json-get.sh" '(() => {
  const tsv = a.tsv.trim();
  const candidates = tsv === "" ? [] : tsv.split("\n").map((line) => {
    const [branch, tip, lastCommitDate, merged, commits, hasLocal] = line.split("\t");
    return {
      branch: branch,
      tip: tip,
      last_commit_date: lastCommitDate,
      merged: merged === "true",
      commits: Number(commits),
      has_local: hasLocal === "true",
    };
  });

  // Same <id>[#-] shape the ref scan used, so a merged PR whose branch was deleted
  // after the merge still counts as proof that the work landed.
  const headPattern = new RegExp("^" + a.task_id + "([#-]|$)");
  let ghError = a.gh_error;
  let prs = [];
  try {
    prs = JSON.parse(a.prs).filter((pr) => headPattern.test(pr.headRefName ?? ""));
  } catch (error) {
    prs = [];
    ghError = ghError === "" ? "unparseable gh output: " + error.message : ghError;
  }

  const unmerged = candidates.filter((candidate) => !candidate.merged);
  const mergedPullRequest = prs.some((pr) => pr.state === "MERGED");
  let state = "none";
  let branch = null;
  if (candidates.some((candidate) => candidate.merged) || mergedPullRequest) {
    state = "complete";
  } else if (unmerged.length === 1) {
    state = "resume";
    branch = unmerged[0].branch;
  } else if (unmerged.length > 1) {
    state = "ambiguous";
  }

  return {
    state: state,
    branch: branch,
    candidates: candidates,
    prs: prs,
    gh_error: ghError === "" ? null : ghError,
  };
})()' task_id="$task_id" prs="$prs_json" gh_error="$gh_error" tsv="$candidates_tsv" <<<'{}'
