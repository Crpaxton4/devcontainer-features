---
name: odoo-task-env
description: "Stand up the working environment for one Odoo task: find work that already exists, create or reuse an isolated git worktree on the task branch, bring a runnable stack up. Use at the START of implementing any task, and whenever one is picked back up."
user-invocable: false
---
# Odoo Task Environment

Three questions, in this order, each answered by one script:

1. Does work for this task already exist? (branches and PRs, both naming conventions)
2. Do the worktree and the branch exist? (create or reuse, never a second one)
3. Is an Odoo stack running? (reuse, restart or create, never a second one)

Inputs: `repo`, `default_branch` and `odoo_version` from the resolved project; the
`task_id`; a `slug` from the task name. `default_branch` is the base for every step.

## Where this runs

| Context | Repos tree | Worktrees | Stacks |
|---|---|---|---|
| Host | `$REPOS_DIR/<repo>` | yes | `stack-ensure.sh` manages docker |
| Devcontainer | not visible; the checkout is bind-mounted at `/mnt/extra-addons` | yes, pass `--repo-path /mnt/extra-addons` | already inside one; docker may be on PATH, `odoo` + postgres decide |

The context is detected container first: `odoo` on PATH and `pg_isready` answering
means devcontainer, whether or not `docker` is on PATH (the devcontainer mounts the
host socket). Only then is `docker` checked for host.

Every script takes `--repo-path DIR` to bypass repos-tree resolution. Inside the
devcontainer it is required.

## Scripts

Every script prints one JSON object as its last stdout line. Write every path in full.

### 0. Preflight (once per machine or after a rebuild)

```bash
<base directory>/scripts/preflight.sh [--soft] [--target-series V] [--custom-tree DIR]
```

The series check fails (exit 1, `failures[]` carries both values) when `ODOO_VERSION` does not start with V.
In a devcontainer, `--custom-tree DIR` also imports every enterprise module in the dependency closure of the modules under DIR (failure `import <name>: <error>`, or `closure: unknown module <name>`).

→ `{"ok","context","failures":[],"warnings":[],"ram_available_gb","repos_dir"}`

Runs every check and reports all failures. On the host: docker, a populated repos tree,
the devcontainer CLI, `gh`. Inside a container: `odoo`, postgres, `gh`. Exit 1 on any
failure, 0 with `--soft`.

### 1. Existing work

```bash
<base directory>/scripts/existing-work.sh <repo> <task_id> <default_branch> [--repo-path DIR]
```

→ `{"state","branch","candidates":[...],"prs":[...],"gh_error"}`

| state | Meaning | Do |
|---|---|---|
| `complete` | a candidate branch merged into base, or a PR MERGED | Stop. Report it. Do not redo. |
| `resume` | exactly one unmerged candidate | Continue on `branch`; pass it as `<resume_branch>` |
| `ambiguous` | two or more unmerged candidates | Stop. List the candidates. Do not choose. |
| `none` | nothing found | Start fresh |

- Both branch forms are probed: `<id>#<slug>` and `<id>-<slug>`.
- `"prs": []` with a non-null `gh_error` means unknown, not "no PRs". Say so.

### 2. Worktree and branch

```bash
<base directory>/scripts/worktree-ensure.sh <repo> <task_id> <slug> <base_branch> [<resume_branch>] [--repo-path DIR]
```

→ `{"worktree","branch","status":"created"|"reused"}`

- Idempotent and flocked per repo. On reuse the branch is read from the worktree.
- A new task branch has no upstream. The first `pr-open.sh` push sets it, so a bare
  `git push` cannot reach the base branch.
- It is the single git writer for the task branch. No command creates a branch.
- This skill records no time. Time comes from the dispatched job row and the hook events.
- `<resume_branch>` is adopted as it stands: never reset, rebased or re-cut.

### 3. Stack

```bash
<base directory>/scripts/stack-ensure.sh <repo> [--target-series NN.0] [--repo-path DIR]
```

→ `{"stack","status":"reused"|"restarted"|"created"|"in-container","stopped_lru":[]}`

- One stack per project, host-globally locked, LRU eviction below `MIN_FREE_GB`.
  Readiness is proved before it returns.
- `in-container` means you are already inside the stack. It reports the core as
  `odoo_version`, the full `odoo --version` line (`Odoo Server 19.0-20260810`), and
  `side_cores`: `[{"series":"18.0","bin","conf","version"}, ...]` for each
  `$ODOO_SRC_ROOT/odooNN-bin` (default `/var/lib/odoo/src`) with `odooNN.conf` beside
  it, `[]` when none. With `--target-series`, a disagreement with the core's major adds
  `"series_mismatch": true` and a `warning`; the exit is still 0. A side core for the
  target series means `run-tests.sh` can still run it.
- `--repo-path DIR` is the repo checkout on the host (replaces `$REPOS_DIR/<repo>`);
  in a container it is accepted and ignored.
- Every exit path prints one JSON line and exits 0, or prints an error on stderr and
  exits non-zero. Exit 0 always comes with JSON: if you see none, your pipe (for
  example `| tail -1`) lost it, so check the exit code.
- `exit 5` — neither a working Odoo container (`odoo` on PATH and `pg_isready`
  answering) nor docker → stop; you are in the wrong place.
- `exit 2` — usage, or on the host the repos tree cannot be resolved (the message names
  `--repo-path` and `REPOS_DIR`), or the repo directory is missing → stop; fix the call.
- Never point it at a worktree path. That mints a second compose project for the repo.

## Re-run

- Reuse the worktree and the branch. Never create a second one.
- Report an open PR before you create anything.
- Report `status` as printed (`created` or `reused`).

## Environment knobs

| Variable | Default | Why you would change it |
|---|---|---|
| `REPOS_DIR` | resolved by `repos-dir.sh` | Non-standard repos tree. A tree with one clone declares itself with `touch <tree>/.odoo-repos-dir` |
| `WORKTREE_SUBDIR` | `.worktrees` | Worktrees live elsewhere in this repo |
| `ODOO_ACTIVE_REPOS` | empty | Protect in-use stacks from LRU eviction |
| `MIN_FREE_GB` | `6` | A different RAM budget |
| `ODOO_SHARED_COMPOSE` | `$REPOS_DIR/.devcontainer/shared/compose.yml` | The shared postgres and proxy stack lives elsewhere |
| `ODOO_REPO_MAP_SCRIPTS` | the sibling map skill's `scripts/` | The map skill moved |

## Stop conditions

- `existing-work.sh` → `complete` or `ambiguous`: stop and report.
- An open PR for this task: stop and report it. Never cut a second branch.
- `stack-ensure.sh` exit 5 or exit 2: stop.
