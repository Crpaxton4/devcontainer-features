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
# One deliberate exception, added after the first real run and widened after the
# second: a refused merge is this script racing the head SHA it just force-
# pushed, not a signal about the PR. merge_node waits (see "Settling"), and
# decides whether to keep waiting from re-queried FACTS rather than from the
# wording of the refusal. Every other step still ends the run unmodified.
#
# Deliberately NOT re-implemented, because the tools already guarantee it:
#
#   conflicts / red checks / unresolved threads     gh pr merge refuses; merge_node
#                                                   then re-queries which it was
#   "did the remote move under us"                  git push --force-with-lease
#   conflict detection and file enumeration         git rebase exits nonzero and
#                                                   leaves itself in progress
#   (was: "is this PR already merged" - gh pr merge refuses. True, but a refusal
#    is not the same as a failure when the PR is already in the state this step
#    exists to produce. See the concurrent-writer note below.)
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

# Unconditional, before either path. Every remote-tracking ref this script reads
# - origin/$BASE for the rebase target and for the base-moved comparison,
# origin/<child> for the checkout the force-push then leases against - has to be
# current, and a resume is precisely the case where they are not: the run stopped,
# a human fixed something, and time passed. Fetching only while creating the state
# file made the first attempt correct and every resume operate on a stale view.
git -C "$REPO" fetch origin --quiet

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
fi

head_branch() { jq -r --arg pr "$1" '.heads[$pr].branch' "$STATE"; }
head_sha() { jq -r --arg pr "$1" '.heads[$pr].sha' "$STATE"; }

# --------------------------------------------------------------------------
# Settling
# --------------------------------------------------------------------------
# The one place this script waits rather than failing. Restacking a child force-
# pushes a new head SHA, which discards every check result and leaves the PR
# with required checks that have not been *registered* yet, let alone run. The
# merge attempt that follows in the same breath is then refused - and so is the
# one after a `gh pr update-branch`, for the same reason.
#
# Two rules, learned across five halts and three separate fixes to this file:
#
#   1. With deleteBranchOnMerge on, GitHub is a CONCURRENT WRITER on every
#      branch and PR this train touches. It retargets orphaned children, deletes
#      merged heads, and recomputes mergeability, all asynchronously, all while
#      this script is issuing its next command. So every step that MUTATES a ref
#      or a PR must succeed when GitHub has already performed it. Not "check
#      whether it is needed, then do it" - that check can never be atomic with
#      the act. Do it, and accept the already-done answer.
#
#   2. Every step that READS a derived property must tolerate GitHub not having
#      computed it yet. `mergeable`, `mergeStateStatus` and check status are all
#      derived, all recomputed after any change to a head or a base, and all
#      transiently UNKNOWN rather than stale-but-valid during that window.
#
# merge_node used to encode rule 2 as a list of retryable refusal STRINGS. Three
# fixes each added one more - #790 "Base branch was modified", #795 "Pull Request
# is not mergeable", #849 "Head branch is out of date" - and the list was the
# bug: GitHub's refusal prose is user-facing copy, not a contract, and nothing
# bounds it. The default is now inverted there. A refusal means "not settled"
# unless a re-queried FACT says the PR is genuinely unmergeable, so a fourth
# unseen phrase costs a wait rather than a dead train.
#
# The two regexes that survive are both rule 1, not rule 2: they match the
# answer to a mutation that was already performed, which is a bounded set.
CHECK_POLL=${STACK_MERGE_CHECK_POLL:-30}
CHECK_TIMEOUT=${STACK_MERGE_CHECK_TIMEOUT:-900}
REF_GONE_RE='Reference does not exist|HTTP 422|Not Found|HTTP 404'
ALREADY_CURRENT_RE='already up[ -]?to[ -]?date|not behind|no new commits'

# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------

# The base branch has `required_status_checks.strict: true` - "require branches
# to be up to date before merging". Every merge advances the base, so every node
# after the first is BEHIND by definition. Children are carried forward by their
# rebase; roots had nothing, so the train stalled on the first root to follow a
# merge, with `enforce_admins: true` closing the --admin hatch gh suggests.
#
# Done server-side rather than by rebasing roots locally, which keeps roots
# exempt from needing a worktree (the assumption the exit-21 guard rests on) and
# avoids a force-push per root. The merge commit this creates is irrelevant: the
# repo is squash-merge only, so it never reaches the base branch.
sync_node() {
    local pr=$1 key out rc
    key="$pr:sync"
    is_done "$key" && return 0

    # A step key added after a state file was written is absent from every node
    # in it, so an older run's finished nodes look un-synced and get re-run
    # against branches that no longer exist. Adding a step must be safe for
    # state files written before that step existed.
    if [[ $(gh pr view "$pr" -R "$SLUG" --json state --jq .state) == MERGED ]]; then
        add_done "$key"
        return 0
    fi

    set_cursor "$key"
    rc=0
    out=$(gh pr update-branch "$pr" -R "$SLUG" 2>&1) || rc=$?
    if ((rc != 0)) && ! printf '%s' "$out" | grep -qiE "$ALREADY_CURRENT_RE"; then
        # Already current is the state this step exists to produce.
        printf '%s\n' "$out" >&2
        exit "$rc"
    fi
    add_done "$key"
}

merge_node() {
    local pr=$1 key out rc waited=0 base_before base_now
    local fatal state mergeable mstate isdraft red review unresolved resync_rc resync_out
    key="$pr:merge"
    is_done "$key" && return 0
    base_before=$(git -C "$REPO" rev-parse "origin/$BASE")

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
        # Same concurrent-writer rule: if the PR is already merged, this step's
        # goal is met and the refusal is about a state we wanted anyway. Reached
        # when a merge lands but the run dies before the done-key is written.
        if [[ $(gh pr view "$pr" -R "$SLUG" --json state --jq .state) == MERGED ]]; then
            out="stack-merge: #$pr was already merged"
            break
        fi

        # The refusal TEXT is not consulted. Three previous fixes each added one
        # more string to a retryable-pattern list - #790 "Base branch was
        # modified", #795 "Pull Request is not mergeable", #849 "Head branch is
        # out of date" - and the fourth shape would have needed a fourth string.
        # The enumeration was the bug: GitHub's refusal prose is user-facing
        # copy, not a contract, and there is no bound on it.
        #
        # So the default is inverted. A refusal is treated as GitHub not having
        # settled UNLESS a re-queried fact says the PR is genuinely unmergeable.
        # Only facts are fatal, and each one below is a specific, checkable
        # state rather than a phrase.
        fatal=""

        state=$(gh pr view "$pr" -R "$SLUG" \
            --json mergeable,mergeStateStatus,isDraft \
            --jq '[.mergeable, .mergeStateStatus, (.isDraft|tostring)] | join(" ")')
        mergeable=$(printf '%s' "$state" | cut -d' ' -f1)
        mstate=$(printf '%s' "$state" | cut -d' ' -f2)
        isdraft=$(printf '%s' "$state" | cut -d' ' -f3)

        # A real conflict. UNKNOWN means "not computed yet", never "no".
        [[ $mergeable == CONFLICTING || $mstate == DIRTY ]] &&
            fatal="#$pr has merge conflicts (mergeable=$mergeable, mergeStateStatus=$mstate)"
        [[ -z $fatal && $isdraft == true ]] &&
            fatal="#$pr is a draft; mark it ready for review first"

        # A red check never goes green by waiting. A pending one is the case
        # this loop exists for, so the two are separated by conclusion, not by
        # rollup state.
        if [[ -z $fatal ]]; then
            red=$(gh pr view "$pr" -R "$SLUG" --json statusCheckRollup --jq '
                [ .statusCheckRollup[]?
                  | select((.conclusion // "") | IN("FAILURE","TIMED_OUT","CANCELLED","ACTION_REQUIRED","STARTUP_FAILURE"))
                  | (.name // .context // "check") ] | join(", ")')
            [[ -n $red ]] && fatal="#$pr has failing checks: $red"
        fi

        # BLOCKED means a rule this script cannot satisfy by waiting - a missing
        # approval, an unresolved thread. But `mergeStateStatus` is itself a
        # DERIVED property, carrying exactly the staleness this file documents
        # for `mergeable`, and GitHub reports BLOCKED transiently while it
        # recomputes a PR whose base just moved. Every merge in a train moves the
        # base, so every node after the first can see it.
        #
        # The first version of this clause concluded "a required review or an
        # unresolved thread" from an ABSENCE - BLOCKED plus no pending checks -
        # and falsely blocked #825 (#852). That absence is satisfied trivially
        # during a recompute, because a node whose checks have all completed
        # legitimately has none pending. On a repo requiring no review at all it
        # could only ever fire as a false positive.
        #
        # So: name the cause or keep waiting. A positive fact, never an absence -
        # the same rule the inversion above rests on, applied one level in.
        if [[ -z $fatal && $mstate == BLOCKED ]]; then
            review=$(gh pr view "$pr" -R "$SLUG" --json reviewDecision --jq '.reviewDecision // ""')
            unresolved=$(gh api graphql -f query="
                { repository(owner: \"$OWNER\", name: \"${SLUG#*/}\") {
                    pullRequest(number: $pr) {
                      reviewThreads(first: 100) { nodes { isResolved } } } } }" \
                --jq '[.data.repository.pullRequest.reviewThreads.nodes[]? | select(.isResolved == false)] | length') || unresolved=0

            if [[ $review == REVIEW_REQUIRED || $review == CHANGES_REQUESTED ]]; then
                fatal="#$pr is BLOCKED by review (reviewDecision=$review), which this script will not merge past"
            elif ((unresolved > 0)); then
                fatal="#$pr is BLOCKED by $unresolved unresolved review thread(s), which this script will not merge past"
            fi
        fi

        # The one base-branch case that is genuinely fatal, kept from #790: the
        # base really advanced, so this node was rebased onto a tip that is no
        # longer current and merging it would land work computed against the
        # wrong history. Decided by comparing tips, not by reading the message.
        if [[ -z $fatal ]]; then
            git -C "$REPO" fetch origin --quiet
            base_now=$(git -C "$REPO" rev-parse "origin/$BASE")
            if [[ $base_now != "$base_before" ]]; then
                printf '%s\n' "$out" >&2
                printf 'stack-merge: %s really did advance, %s -> %s.\n' \
                    "$BASE" "${base_before:0:7}" "${base_now:0:7}" >&2
                printf '  #%s was rebased onto the older tip, so its rebase is stale.\n' "$pr" >&2
                printf '  Re-rebase it before retrying: this script will not silently\n' >&2
                printf '  merge a node computed against a base that has moved.\n' >&2
                exit "$rc"
            fi
        fi

        # BEHIND is the one non-fatal state that waiting does NOT clear. It is a
        # real answer with a real remedy - re-sync - and the inversion above
        # would otherwise sit on it for the whole timeout and then fail.
        #
        # Reachable because `$pr:sync` is a done-key: once recorded, sync_node
        # returns immediately, so a base that advances AFTER that key is written
        # leaves the node permanently behind with nothing left to fix it. Any
        # merge landing between this node's sync and its merge does exactly that,
        # which on a multi-node train is the normal case rather than a rare one.
        if [[ -z $fatal && $mstate == BEHIND ]]; then
            resync_rc=0
            resync_out=$(gh pr update-branch "$pr" -R "$SLUG" 2>&1) || resync_rc=$?
            if ((resync_rc != 0)) &&
                ! printf '%s' "$resync_out" | grep -qiE "$ALREADY_CURRENT_RE"; then
                printf '%s\n' "$resync_out" >&2
                exit "$resync_rc"
            fi
            printf 'stack-merge: #%s was BEHIND; re-synced and retrying\n' "$pr" >&2
        fi

        if [[ -n $fatal ]] || ((waited >= CHECK_TIMEOUT)); then
            printf '%s\n' "$out" >&2
            if [[ -n $fatal ]]; then
                printf 'stack-merge: %s\n' "$fatal" >&2
            else
                printf 'stack-merge: #%s never settled in %ss. Last observed: mergeable=%s mergeStateStatus=%s\n' \
                    "$pr" "$CHECK_TIMEOUT" "$mergeable" "$mstate" >&2
            fi
            exit "$rc"
        fi
        printf 'stack-merge: #%s not settled yet (%ss/%ss, mergeable=%s %s), waiting %ss: %s\n' \
            "$pr" "$waited" "$CHECK_TIMEOUT" "$mergeable" "$mstate" "$CHECK_POLL" "${out%%$'\n'*}" >&2
        sleep "$CHECK_POLL"
        waited=$((waited + CHECK_POLL))
    done
    printf '%s\n' "$out"
    add_done "$key"

    git -C "$REPO" fetch origin --quiet
}

# Runs only after every child of $pr has been retargeted and pushed.
delete_merged_branch() {
    local pr=$1 branch key out rc
    key="$pr:delete-branch"
    is_done "$key" && return 0

    branch=$(head_branch "$pr")
    set_cursor "$key"
    # Act, then check - never check, then act. A pre-check cannot be atomic with
    # the delete: with deleteBranchOnMerge on, GitHub is deleting this same ref
    # asynchronously from the merge that just happened, so ls-remote can still
    # see it while the DELETE that follows returns 422. Absent is the end state
    # this step wants, however the ref got there.
    rc=0
    out=$(gh api -X DELETE "repos/$SLUG/git/refs/heads/$branch" --silent 2>&1) || rc=$?
    if ((rc != 0)) && ! printf '%s' "$out" | grep -qE "$REF_GONE_RE"; then
        printf '%s\n' "$out" >&2
        exit "$rc"
    fi
    add_done "$key"
}

restack_child() {
    local child=$1 parent=$2 child_branch parent_sha current_base
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
        # Often already done for us. With the repo's deleteBranchOnMerge on,
        # GitHub deletes the parent's head at merge time and retargets the
        # orphaned child onto that branch's own base - which is this BASE. The
        # script never gets to choose, so it must tolerate arriving second.
        #
        # `gh pr edit --base` rejects a no-op retarget with "A pull request
        # already exists for base branch X and head branch Y", naming the PR
        # being edited as though it collided with a different one. That reads
        # like a duplicate-PR problem and is not one.
        current_base=$(gh pr view "$child" -R "$SLUG" --json baseRefName --jq .baseRefName)
        if [[ $current_base != "$BASE" ]]; then
            gh pr edit "$child" -R "$SLUG" --base "$BASE"
        fi
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
    printf 'then re-run this identical command. No PR, branch or worktree has\n' >&2
    printf 'been changed.\n' >&2
    exit 21
fi

# One reusable ops worktree per run rather than one per child: fewer moving
# parts, and it is the path recorded in the state file for a human or an agent
# to go and resolve a conflict in.
#
# Created here, after the availability guard, rather than alongside the state
# file: exit 21 promises nothing was changed, and creating a worktree first made
# that promise false. Guarded on the path because a resume inherits the worktree
# the first attempt left behind, mid-rebase and all.
if [[ ! -d $OPS ]]; then
    git -C "$REPO" worktree add --detach "$OPS" "origin/$BASE"
fi

# --------------------------------------------------------------------------
# Train
# --------------------------------------------------------------------------

for pr in "${ORDER[@]}"; do
    sync_node "$pr"
    merge_node "$pr"
    for child in "${ORDER[@]}"; do
        [[ ${PARENT[$child]} == "$pr" ]] || continue
        restack_child "$child" "$pr"
    done
    delete_merged_branch "$pr"
done

# Exit 0 is a promise the caller acts on - Phase 8 says "exit 0 means the stack
# landed - go to Phase 9", and Phase 9 reaps. A bug that let the script exit 0
# without landing the stack would have the caller reap and report success over
# six unmerged PRs. So the promise is checked rather than assumed.
unlanded=""
for pr in "${ORDER[@]}"; do
    [[ $(gh pr view "$pr" -R "$SLUG" --json state --jq .state) == MERGED ]] ||
        unlanded+=" #$pr"
done
if [[ -n $unlanded ]]; then
    printf 'stack-merge: reached the end of the train with unmerged PRs:%s\n' "$unlanded" >&2
    printf '  This is a bug in this script - it should have stopped at the first\n' >&2
    printf '  failure. Nothing has been reaped. Do not treat the stack as landed.\n' >&2
    exit 23
fi

set_cursor ""
mark_complete

# The run created this worktree, so the run removes it. Unreaped worktrees are
# how this repo accumulated forty stale agent-* entries.
git -C "$REPO" worktree remove --force "$OPS"
git -C "$REPO" worktree prune

printf 'stack-merge: %s landed on %s (%s)\n' "$ID" "$BASE" "$SLUG"
