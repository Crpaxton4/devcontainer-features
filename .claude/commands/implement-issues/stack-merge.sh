#!/usr/bin/env bash
#
# stack-merge.sh - deterministic, resumable merge train for a stacked-PR run.
#
# Owns every mutation in the merge train (merge, rebase, retarget, force-push)
# so the repeatable part of /implement-issues stops depending on model
# judgement. Hand-driving the same thirteen rebase -> force-push -> "merged,
# continue" iterations is where the drift came from.
#
# Design rule: run the same commands a person would run by hand, in a fixed
# order, with a checkpoint after each. Do not wrap them. `set -euo pipefail`
# means the first failing git or gh command ends the run with its own exit
# status and its own stderr, unmodified - no `||`, no retries, no error
# translation, no re-checking of conditions the tools already enforce.
#
# One deliberate exception, added after the first real run: a merge refused
# because required checks have not registered or finished yet is this script
# racing the head SHA it just force-pushed, not a signal about the PR. It waits
# (see "Check settling"). Every other refusal still ends the run unmodified.
#
# Deliberately NOT re-implemented, because the tools already guarantee it:
#
#   green-checks / mergeable / unresolved threads   gh pr merge refuses, and says why
#   "did the remote move under us"                  git push --force-with-lease
#   conflict detection and file enumeration         git rebase exits nonzero and
#                                                   leaves itself in progress
#   "is this PR already merged"                     gh pr merge refuses
#   squash-only, linear history, thread resolution  the main ruleset
#   conventional PR title                           pr-title-lint
#   wrong-account push                              403 from the pinned GH_TOKEN
#
# The only invariants enforced here are the ones no tool knows about: the
# argument signature, and that a stack cannot be silently re-planned under an
# id that is already in flight (exit 20).
#
# Usage:
#   stack-merge.sh --repo <abs-path> --id <id> --stack <pr>[:<parent-pr>] ...
#                  [--base <branch>] [--status]
#
#   --stack 770 771:770 772:771 773 774
#       770 root, 771 child of 770, 772 child of 771, 773 and 774 roots.
#       An explicit ordered list, never discovery - so release-please and
#       Dependabot PRs cannot leak in without needing a filter to exclude them.

set -euo pipefail

usage() {
    cat <<'EOF'
usage: stack-merge.sh --repo <abs-path> --id <id> --stack <pr>[:<parent-pr>] ...
                      [--base <branch>] [--status]

  --repo    absolute path to the checkout (required; never defaults to $PWD)
  --id      run id; state lives at <repo>/.claude/stacks/<id>.json
  --stack   ordered PR list, parents before children, <pr>:<parent-pr> for a child
  --base    branch the stack lands on (default: main)
  --status  print the state file and exit; read-only

exit 0   the stack landed
exit 2   bad arguments
exit 20  this --id is already in flight with a different stack; nothing mutated
other    the raw exit status of the git or gh command that failed
EOF
}

# --------------------------------------------------------------------------
# Arguments
# --------------------------------------------------------------------------

REPO=""
ID=""
BASE="main"
STATUS_ONLY=0
STACK=()

while (($#)); do
    case "$1" in
    --repo)
        REPO=${2:-}
        shift 2
        ;;
    --id)
        ID=${2:-}
        shift 2
        ;;
    --base)
        BASE=${2:-}
        shift 2
        ;;
    --status)
        STATUS_ONLY=1
        shift
        ;;
    --stack)
        shift
        while (($#)) && [[ $1 != --* ]]; do
            STACK+=("$1")
            shift
        done
        ;;
    -h | --help)
        usage
        exit 0
        ;;
    *)
        printf 'stack-merge: unknown argument: %s\n\n' "$1" >&2
        usage >&2
        exit 2
        ;;
    esac
done

die() {
    printf 'stack-merge: %s\n' "$1" >&2
    exit "${2:-2}"
}

# --repo is required and absolute on purpose: the script must be impossible to
# run against the wrong checkout by accident, so there is no $PWD fallback.
[[ -n $REPO ]] || die "--repo is required (absolute path, no default)"
[[ $REPO == /* ]] || die "--repo must be an absolute path, got: $REPO"
[[ -d $REPO/.git || -f $REPO/.git ]] || die "--repo is not a git checkout: $REPO"
[[ -n $ID ]] || die "--id is required"

STATE_DIR=$REPO/.claude/stacks
STATE=$STATE_DIR/$ID.json

if ((STATUS_ONLY)); then
    cat "$STATE"
    exit 0
fi

((${#STACK[@]})) || die "--stack is required"

# Parse <pr>[:<parent-pr>] into an ordered list plus a parent map.
declare -A PARENT=()
ORDER=()
for token in "${STACK[@]}"; do
    pr=${token%%:*}
    parent=""
    [[ $token == *:* ]] && parent=${token#*:}
    [[ $pr =~ ^[0-9]+$ ]] || die "not a PR number: $token"
    [[ -z $parent || $parent =~ ^[0-9]+$ ]] || die "not a parent PR number: $token"
    [[ -z ${PARENT[$pr]+set} ]] || die "PR #$pr appears twice in --stack"
    PARENT[$pr]=$parent
    ORDER+=("$pr")
done

# Parents must be in the stack and must come first - bottom-up is the whole
# point, and a child listed before its parent would rebase onto a base that has
# not moved yet.
seen=()
for pr in "${ORDER[@]}"; do
    parent=${PARENT[$pr]}
    if [[ -n $parent ]]; then
        [[ ${PARENT[$parent]+set} ]] || die "#$pr names parent #$parent, which is not in --stack"
        [[ " ${seen[*]} " == *" $parent "* ]] || die "#$pr is listed before its parent #$parent"
    fi
    seen+=("$pr")
done

# --------------------------------------------------------------------------
# Identity, derived from the repo - never configured, never passed in
# --------------------------------------------------------------------------
#
# There is no --as / --owner flag by design. The owner comes from the repo's own
# origin remote, so it cannot drift from the repo being operated on and cannot
# be pointed at the wrong account by a stale argument. Pinning GH_TOKEN is the
# enforceable form of "always act as the repo owner": gh has no global --user
# flag, but GH_TOKEN overrides the active account for every subsequent gh call.
# If that owner has no authenticated account, `gh auth token` exits nonzero and
# `set -e` stops the run loudly, before anything is mutated.

SLUG=$(git -C "$REPO" remote get-url origin |
    sed -E 's#^(https?://[^/]+/|git@[^:]+:|ssh://git@[^/]+/)##; s#\.git$##; s#/$##')
OWNER=${SLUG%%/*}
GH_TOKEN=$(gh auth token --user "$OWNER")
export GH_TOKEN

# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

# Normalized so that argument order and whitespace cannot change the signature,
# but the set of (pr, parent) edges can.
NORMALIZED=$(printf '%s\n' "${STACK[@]}" | LC_ALL=C sort | tr '\n' ' ')
SIGNATURE=$(printf '%s|%s|%s' "$REPO" "$BASE" "$NORMALIZED" | sha1sum | cut -d' ' -f1)

# State is rewritten atomically: jq into a temp file alongside it, then rename.
# If jq fails, `set -e` stops before the rename and the previous state survives
# intact - a truncated state file is the one failure a resume could not see.
# Each rewrite calls jq directly rather than through a wrapper, so the jq
# program stays a literal argument to jq.
state_tmp() {
    mktemp "$STATE_DIR/.$ID.XXXXXX"
}

is_done() {
    jq -e --arg k "$1" '.done | index($k)' "$STATE" >/dev/null
}

add_done() {
    local tmp
    tmp=$(state_tmp)
    jq --arg k "$1" '.done += [$k]' "$STATE" >"$tmp"
    mv "$tmp" "$STATE"
}

set_cursor() {
    local tmp
    tmp=$(state_tmp)
    jq --arg c "$1" '.cursor = (if $c == "" then null else $c end)' "$STATE" >"$tmp"
    mv "$tmp" "$STATE"
}

mark_complete() {
    local tmp
    tmp=$(state_tmp)
    jq '.complete = true' "$STATE" >"$tmp"
    mv "$tmp" "$STATE"
}

if [[ -f $STATE ]]; then
    prev=$(jq -r '.signature' "$STATE")
    # The one bespoke exit code: same id, different stack. Nothing is mutated.
    # This is the guard against a model quietly re-planning between attempts.
    if [[ $prev != "$SIGNATURE" ]]; then
        printf 'stack-merge: --id %s is already in flight with a different stack.\n' "$ID" >&2
        printf '  recorded signature: %s\n  this invocation:    %s\n' "$prev" "$SIGNATURE" >&2
        printf '  re-plan deliberately or use a new --id. Nothing was changed.\n' >&2
        exit 20
    fi
    OPS=$(jq -r '.ops_worktree' "$STATE")
else
    mkdir -p "$STATE_DIR"
    OPS=$STATE_DIR/ops-$ID

    git -C "$REPO" fetch origin --quiet

    # Head branch and tip SHA are captured once, up front, while every PR in the
    # stack is still open. A merged parent's branch is deleted, so the SHA is
    # what a child rebases off later - recorded rather than re-derived, so a
    # resume cannot see a different value than the first attempt did.
    heads='{}'
    for pr in "${ORDER[@]}"; do
        branch=$(gh pr view "$pr" -R "$SLUG" --json headRefName --jq .headRefName)
        sha=$(git -C "$REPO" rev-parse "origin/$branch")
        heads=$(printf '%s' "$heads" |
            jq --arg pr "$pr" --arg b "$branch" --arg s "$sha" '.[$pr] = {branch: $b, sha: $s}')
    done

    jq -n \
        --arg id "$ID" --arg sig "$SIGNATURE" --arg repo "$SLUG" \
        --arg base "$BASE" --arg ops "$OPS" --argjson heads "$heads" \
        '{id: $id, signature: $sig, repo: $repo, base: $base,
          ops_worktree: $ops, heads: $heads, done: [], cursor: null}' >"$STATE"

    # One reusable ops worktree per run rather than one per child: fewer moving
    # parts, and it is the path recorded in the state file for a human or an
    # agent to go and resolve a conflict in.
    git -C "$REPO" worktree add --detach "$OPS" "origin/$BASE"
fi

head_branch() { jq -r --arg pr "$1" '.heads[$pr].branch' "$STATE"; }
head_sha() { jq -r --arg pr "$1" '.heads[$pr].sha' "$STATE"; }

# --------------------------------------------------------------------------
# Check settling
# --------------------------------------------------------------------------
# The one place this script waits rather than failing. Restacking a child force-
# pushes a new head SHA, which discards every check result and leaves the PR
# with required checks that have not been *registered* yet, let alone run. The
# merge attempt that follows in the same breath then fails with "Required status
# check ... is expected".
#
# That is not `gh pr merge` refusing on a red or contested PR, which is a real
# signal and still stops the run. It is this script racing its own push. Waiting
# for the checks it just invalidated is the script cleaning up after itself.
CHECK_POLL=${STACK_MERGE_CHECK_POLL:-30}
CHECK_TIMEOUT=${STACK_MERGE_CHECK_TIMEOUT:-900}
CHECK_PENDING_RE='is expected|Required status check|checks are pending|not yet complete|still (running|pending)|in progress'

# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------

merge_node() {
    local pr=$1 key out rc waited=0
    key="$pr:merge"
    is_done "$key" && return 0

    set_cursor "$key"
    # Deliberately NOT --delete-branch. Deleting the head ref here deletes the
    # base branch of every child PR still pointing at it, and GitHub closes a
    # PR whose base branch disappears - before restack_child can retarget it.
    # The branch is dropped by delete_merged_branch once the children are off it.
    while :; do
        if out=$(gh pr merge "$pr" -R "$SLUG" --squash 2>&1); then
            break
        else
            rc=$?
        fi
        if ((waited >= CHECK_TIMEOUT)) || ! printf '%s' "$out" | grep -qE "$CHECK_PENDING_RE"; then
            printf '%s\n' "$out" >&2
            exit "$rc"
        fi
        printf 'stack-merge: #%s checks not settled (%ss/%ss), waiting %ss\n' \
            "$pr" "$waited" "$CHECK_TIMEOUT" "$CHECK_POLL" >&2
        sleep "$CHECK_POLL"
        waited=$((waited + CHECK_POLL))
    done
    printf '%s\n' "$out"
    add_done "$key"

    git -C "$REPO" fetch origin --quiet
}

# Runs only after every child of $pr has been retargeted and pushed.
delete_merged_branch() {
    local pr=$1 branch key
    key="$pr:delete-branch"
    is_done "$key" && return 0

    branch=$(head_branch "$pr")
    set_cursor "$key"
    # A ref that is already gone is the desired end state, not a failure: a
    # resumed run can reach here after an earlier attempt (or a human) removed
    # it. Anything other than "absent" still fails loudly.
    if git ls-remote --exit-code --heads "$(git -C "$REPO" remote get-url origin)" \
        "refs/heads/$branch" >/dev/null 2>&1; then
        gh api -X DELETE "repos/$SLUG/git/refs/heads/$branch" --silent
    fi
    add_done "$key"
}

restack_child() {
    local child=$1 parent=$2 child_branch parent_sha
    child_branch=$(head_branch "$child")
    parent_sha=$(head_sha "$parent")

    if ! is_done "$child:rebase"; then
        # Resume is a lookup, not a probe. If the rebase was already started,
        # git left it in progress in the ops worktree and this continues it;
        # the script never inspects the worktree to guess which case it is in.
        if is_done "$child:rebase-started"; then
            set_cursor "$child:rebase"
            git -C "$OPS" rebase --continue
        else
            git -C "$OPS" checkout -B "$child_branch" "origin/$child_branch"
            add_done "$child:rebase-started"
            set_cursor "$child:rebase"
            git -C "$OPS" rebase --onto "origin/$BASE" "$parent_sha"
        fi
        add_done "$child:rebase"
    fi

    if ! is_done "$child:retarget"; then
        set_cursor "$child:retarget"
        gh pr edit "$child" -R "$SLUG" --base "$BASE"
        add_done "$child:retarget"
    fi

    if ! is_done "$child:push"; then
        set_cursor "$child:push"
        git -C "$OPS" push --force-with-lease origin "$child_branch"
        add_done "$child:push"
    fi
}

# --------------------------------------------------------------------------
# Branch availability
# --------------------------------------------------------------------------
# git allows a branch to be checked out in exactly one worktree at a time. The
# ops worktree has to check each CHILD branch out to rebase it, so a child still
# held by the worker worktree that built it makes the train impossible. Roots
# are exempt: they only ever get `gh pr merge`, which is server-side.
#
# Checked here rather than discovered at the rebase, because discovering it
# there means failing mid-train with a root already merged and its children
# stranded. Nothing below this point is reached unless every child is free.

held=$(git -C "$REPO" worktree list --porcelain | awk -v ops="$OPS" '
    /^worktree /{ wt = $2 }
    /^branch /  { b = $2; sub(/^refs\/heads\//, "", b); if (wt != ops) print b }
')

blocked=""
for pr in "${ORDER[@]}"; do
    [[ -n ${PARENT[$pr]:-} ]] || continue
    b=$(head_branch "$pr")
    if printf '%s\n' "$held" | grep -qxF "$b"; then
        blocked+="  $b (PR #$pr)"$'\n'
    fi
done

if [[ -n $blocked ]]; then
    printf 'stack-merge: these child branches are checked out in other worktrees:\n' >&2
    printf '%s' "$blocked" >&2
    printf 'The ops worktree must check each one out to rebase it, and git permits\n' >&2
    printf 'only one worktree per branch. Remove those worktrees:\n' >&2
    printf '  git -C %s worktree remove <path>\n' "$REPO" >&2
    printf 'then re-run this identical command. Nothing has been changed.\n' >&2
    exit 21
fi

# --------------------------------------------------------------------------
# Train
# --------------------------------------------------------------------------

for pr in "${ORDER[@]}"; do
    merge_node "$pr"
    for child in "${ORDER[@]}"; do
        [[ ${PARENT[$child]} == "$pr" ]] || continue
        restack_child "$child" "$pr"
    done
    delete_merged_branch "$pr"
done

set_cursor ""
mark_complete

# The run created this worktree, so the run removes it. Unreaped worktrees are
# how this repo accumulated forty stale agent-* entries.
git -C "$REPO" worktree remove --force "$OPS"
git -C "$REPO" worktree prune

printf 'stack-merge: %s landed on %s (%s)\n' "$ID" "$BASE" "$SLUG"
