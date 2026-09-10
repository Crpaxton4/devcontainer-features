---
name: odoo-task-env
description: "Stand up the working environment for one Odoo task: find any work that already exists for it, then create or reuse an isolated git worktree on the task branch, and make sure a runnable Odoo stack is up. Use this at the START of implementing any Odoo task — before writing code, before cutting a branch, before running anything — and whenever picking a task back up, so existing branches and open PRs are found instead of duplicated. Also use it when the user asks where to work on a task, whether a task was already started, or to set up/reset a task branch or dev stack."
when_to_use: At the start of implementing any Odoo task, and whenever a task is picked back up; before writing code, cutting a branch, or running anything; someone asks where to work on a task, whether it was already started, or whether a branch or an open PR already exists for it; or a task branch or a dev stack has to be set up or reset.
user-invocable: false
---
# Odoo Task Environment

Everything between "here is a task id" and "there is somewhere to write code". Three questions, in this order, each with script that answer deterministically:

1. Work for this task already exist? (branches, PRs — both naming conventions)
2. Worktree and branch exist? (create or reuse — never second one)
3. Odoo stack running? (reuse, restart, or create — never second one)

Order matter. Cut branch before question 1 = task finished last month get implemented second time, on branch beside one that already have open PR.

Live values, injected every time this skill is rendered — at invocation and at every
agent spawn that preloads it. Trust them; do not re-derive them.

- **Worktrees in this checkout**: !`git worktree list 2>/dev/null | tail -n +2 | sed -n 's/.*\[\(.*\)\]$/\1/p' | paste -sd ' ' - | grep . || echo "none visible from here"`
- **Open pull requests visible from here**: !`timeout 1 gh pr list --state open --limit 10 --json number,headRefName --jq '.[] | "#\(.number) \(.headRefName)"' 2>/dev/null | paste -sd ' ' - | grep . || echo "none listed — gh may be slow, logged out, or this is not the project checkout"`

Neither line answers question 1; `existing-work.sh` does, because it reads both
naming conventions and finds merged pull requests whose branch has since been
deleted. The two lines are an early warning instead. A task id that already appears
on either of them is work in flight, so stop and find it rather than cutting a
second branch beside it.

## Preconditions

Resolve project first with **`odoo-dev:odoo-repo-map`** — need `repo`, `default_branch`, `odoo_version`. Do not read them off checkout.

Load **`odoo-dev:odoo-devcontainer`** for paths, CLI, environment layout.

## Where this runs

Two contexts, scripts detect which one they in:

| Context | Repos tree | Worktrees | Stacks |
|---|---|---|---|
| **Host** | `$REPOS_DIR/<repo>` | yes | `stack-ensure.sh` manages docker |
| **Devcontainer** | not visible — checkout bind-mounted at `/mnt/extra-addons` | yes, pass `--repo-path /mnt/extra-addons` | nothing to do; already inside one |

Every script take `--repo-path DIR` to bypass repos-tree resolution entirely. Inside devcontainer not optional — no repos tree to resolve.

## Scripts

This skill's scripts (`ENV_SCRIPTS`) live at `<base directory>/scripts`, where
`<base directory>` is the absolute path on the `Base directory for this skill:`
line injected above this body. The name is a label for that directory, not a shell
variable to set and reuse: every Bash call has to spell the absolute path out in
full, because a Bash call inherits no environment and keeps no state from the call
before it.

Each print one JSON object as last stdout line.

### 0. Preflight (once per machine or after rebuild)

```bash
<base directory>/scripts/preflight.sh [--soft]
```

→ `{"ok","context","failures":[],"warnings":[],"ram_available_gb","repos_dir"}`

Run every check, not stop at first — useful answer is "these three things wrong", not first one three times. Detect own context: on host check docker, populated repos tree, devcontainer CLI, `gh`; inside container check `odoo`, postgres, `gh`. Exit 1 on any failure, or 0 with `--soft`.

`odoo-mcp` reachability deliberately not checked here — MCP not callable from bash. Check from session.

### 1. Existing work

```bash
<base directory>/scripts/existing-work.sh <repo> <task_id> <default_branch> [--repo-path DIR]
```

→ `{"state","branch","candidates":[...],"prs":[...],"gh_error","tracking":[...],"tracking_error"}`

`state` computed, never judged:

| state | Meaning | Do |
|---|---|---|
| `complete` | Candidate branch merged into base, or PR MERGED | Stop. Report it. Work landed — confirm with user before redoing anything |
| `resume` | Exactly one unmerged candidate | Continue on `branch` — pass as `<resume_branch>` below |
| `ambiguous` | Two or more unmerged candidates | Stop, let user pick. Do not choose for them |
| `none` | Nothing found | Start fresh |

Probe **both** branch forms: `<id>#<slug>` (humans push) and `<id>-<slug>` (automation create). Match only one = five commits and open PR become invisible. `gh` failure non-fatal, degrade to `"prs": []` plus `gh_error` string — say so in report, not treat "no PRs" as proof.

`tracking` = this task's active local tracking sessions, from best-effort `odoo-sdk cmd task_status` (argless — the script filters to the task). Same degrade shape as `gh`: CLI missing or failing → `"tracking": []` plus `tracking_error` string. `[]` with non-null `tracking_error` means **unknown**, not "no session" — say so in report.

### 2. Worktree and branch

```bash
<base directory>/scripts/worktree-ensure.sh <repo> <task_id> <slug> <base_branch> [<resume_branch>] [--repo-path DIR]
```

→ `{"worktree","branch","status":"created"|"reused","tracking":"started"|"already_running"|"skipped"|"error"}`

Idempotent, serialized per repo under flock so concurrent callers cannot race `git worktree add`. On reuse branch **read from worktree**, never recomputed from arguments — renamed Odoo task yield new slug, and reporting branch never created hand you something you cannot push.

**Session-FSM alignment.** `worktree-ensure.sh` is the **single git writer** for the task branch. After the worktree flow succeeds it calls the registry `odoo-sdk cmd start_task` best-effort (that command is git-free — branch setup lives only in the MCP tool layer), so the local tracking session opens with the worktree; `tracking` in its output say what happened, and `skipped`/`error` warn without failing the flow — do not retry the worktree over it, just say so in report. The interactive odoo-mcp `start_task` tool also create `<id>-<slug>` branch. If tracking session started that way, pass that branch as `<resume_branch>` so this adopt it. Two branches for one task = two half-finished heads, no PR.

`<resume_branch>` adopted exactly as it stands — never reset, rebased, re-cut. Commits on it are work you continuing.

### 3. Stack

```bash
<base directory>/scripts/stack-ensure.sh <repo>
```

→ `{"stack","status":"reused"|"restarted"|"created"|"in-container","stopped_lru":[]}`

One stack per project, host-globally locked, LRU eviction below `MIN_FREE_GB` (default 6). Protect stacks you using with `ODOO_ACTIVE_REPOS=repoA,repoB`. Readiness proved (postgres reachable *from inside* odoo container, `odoo --version` answers) before return, so `reused` never mean "still coming up".

`in-container` mean you already inside stack; nothing done, nothing needed. Exit 5 mean docker absent **and** this not working Odoo container — you in wrong place, not looking at broken script.

Never point this at worktree path: would mint second compose project for same repo and break the singleton it exist to guarantee.

## The rerun contract

Run this skill twice for one task must converge, not accumulate:

- Reuse worktree and branch; do not create second one.
- **Surface open PRs before creating anything.** Open PR for this task is most important thing on screen.
- Report `status` honestly (`created` vs `reused`) — downstream steps read it.

## Environment knobs

| Variable | Default | Why you would change it |
|---|---|---|
| `REPOS_DIR` | resolved by `odoo-repo-map/scripts/repos-dir.sh` | Non-standard repos tree |
| `WORKTREE_SUBDIR` | `.worktrees` | Worktrees live elsewhere in this repo |
| `ODOO_ACTIVE_REPOS` | empty | Protect in-use stacks from LRU eviction |
| `ODOO_TASK_TRACKING` | `1` | `0` disables the best-effort `odoo-sdk` tracking probes (existing-work `tracking`, worktree `start_task`) |
| `MIN_FREE_GB` | `6` | Different RAM budget |
| `ODOO_SHARED_COMPOSE` | `$REPOS_DIR/.devcontainer/shared/compose.yml` | Shared postgres/proxy stack lives elsewhere |
| `ODOO_REPO_MAP_SCRIPTS` | sibling `../odoo-repo-map/scripts` | Map skill moved |

## Next

Implement, then **`odoo-dev:odoo-test-run`** for evidence, then **`odoo-dev:odoo-pr`**. Commit messages follow `odoo-devcontainer/references/commits.md`; design follow `odoo-dev:principles`.

## Verify

```bash
bash <base directory>/scripts/tests/task-env.test.sh   # offline; builds throwaway git repos in a temp dir
```