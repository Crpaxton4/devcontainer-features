---
name: odoo-repo-map
description: "Resolve an Odoo project to its git repo, base branch, Odoo series, and environment chain (dev → staging → production). Use before cutting a worktree, opening a PR, or planning a release, and when a project is added or renamed."
user-invocable: false
---
# Odoo Repo Map

One answer to "this Odoo project: which repo, which branch, which series, what comes after
this branch?". Nothing is inferred from a checkout: the GitHub default branch is not the
base, the folder name is not the project name, and the chain lives nowhere in git.

The scripts here are thin wrappers over `odoo-dev repo-map`. The CLI validates, backs up
and writes the map.

## The data

`${ODOO_DEV_HOME:-$HOME/.config/odoo-dev}/repo-map.json`. The devcontainer sets
`ODOO_DEV_HOME`. The first write creates the file. A map that does not parse exits 4 naming
the complaint. It is never reseeded.

The file holds `defaults` and one entry per exact Odoo `project.project` name:

| Key | Meaning |
|-----|---------|
| `repo` | A folder name under the repos tree, or an absolute path to the checkout |
| `odoo_version` | The series this project runs, e.g. `18.0` |
| `aliases` | Other `project.project` names that resolve to this entry. Never an entry name. Each alias appears once in the map |
| `branch_flow` | Ordered environment chain, e.g. `["task","UAT","main"]`. Empty means `defaults.branch_flow` |
| `upgrade_branch` | Object of target series to branch, e.g. `{"19.0":"19-upgrade"}`. A series with no key uses the last element of the flow |
| `release_assignee` | GitHub login a release PR is assigned to. Empty means the default |
| `release_reviewer` | GitHub login the release PR requests review from, or `none`. Empty means the default |

`defaults` holds `branch_flow` (initially `["task","E2E","UAT","main"]`), `release_assignee`
and `release_reviewer`. Change them with `odoo-dev repo-map defaults`.

Facts:

- `task` is only ever element 0 of `branch_flow`. It stands for the per-task branch.
- The base branch of a task is the first element after `task` that exists on `origin`.
  A missing branch is skipped.
- The last element of `branch_flow` is production.
- The keys of older maps that no longer exist are dropped by the reader. The default branch
  is derived from the flow, never stored.
- A `.odoo-repos-dir` marker file declares a repos tree that holds one clone. It outranks
  the two-git-subdirectories sweep. `REPOS_DIR` is the per-shell equivalent.

## Scripts

Write every path in full.

| Script and arguments | Prints | Exit |
|---|---|---|
| `repos-dir.sh [--raw]` | `{"repos_dir"}` from `$REPOS_DIR`, then the marker, then the sweep | 0 · 1 no tree (an absolute `repo` skips it) |
| `repo-map.sh get "<name>" \| list \| validate \| remove "<name>"` [`--json`] | the CLI output of the same verb | 0 · 2 usage · 3 unmapped · 4 map invalid |
| `repo-map.sh add "<name>" <repo> --odoo-version V [--aliases A,B] [--branch-flow task,UAT,main] [--upgrade-branch 19.0=BRANCH]... [--release-assignee X] [--release-reviewer Y]` | `odoo-dev repo-map set` | 0 · 2 usage or already mapped · 4 invalid |
| `repo-map.sh set "<name>" [--repo R] [--odoo-version V] [same fields]` | `odoo-dev repo-map set`, keeping every field not named | 0 · 2 · 3 · 4 |
| `repo-map.sh set-flow "<name>" "task,UAT,main"` | `set` with `--branch-flow`. `--flow-confirmed` is accepted and ignored | 0 · 2 · 3 · 4 |
| `repo-map.sh set-release-owners "<name>" <assignee> <reviewer\|none>` | `set` with the two logins | 0 · 2 · 3 · 4 |
| `project-resolve.sh "<project\|alias\|repo>" [--next-after <branch>] [--series NN.0]` | `{"project","repo","repo_root","origin","odoo_version","branch_flow","base_branch","upgrade_branch","next_env","at_production","release_assignee","release_reviewer"}` | 0 · 2 usage or a `--series` that is not `NN.0` · 3 unmapped · 4 `--next-after` names a branch not in the flow · 6 ambiguous |
| `project-bootstrap.sh "<checkout path>" [--project NAME]` | the entry it wrote, as JSON | 0 · 2 not a git checkout or no series found · 3 already mapped (entry printed) |

JSON helper: `plugins/odoo-dev/scripts/json-get.sh '<expression on d>'` reads JSON on stdin.
Use it to read one field from any output above.

### Resolve

```bash
<base directory>/scripts/project-resolve.sh "B&K Logistics Support"
<base directory>/scripts/project-resolve.sh "B&K Logistics Support" --next-after task   # -> next_env: "UAT"
```

`--next-after` takes an element of the chain. The first hop is `--next-after task`. `repo_root`
and `base_branch` are `null` when no checkout is on this machine. `branch_flow` is the
effective flow (the entry's own, else the defaults). The two `release_*` fields fall back to the
defaults and are `null` when neither is set.

- `exit 3` — unmapped → run `project-bootstrap.sh "<checkout>"`, then resolve again by
  the project name it printed; still 3 → stop, report the path and the output.
- `exit 4` — the branch is not in the flow → stop; report the flow.
- `exit 6` — ambiguous: one repo folder maps to several projects → stop; list the
  candidate project names; never pick one.
- `exit 2` — usage → fix the call.

### Bootstrap an unmapped checkout

```bash
<base directory>/scripts/project-bootstrap.sh /mnt/extra-addons --project "ACME Support"
```

It records `repo` and `odoo_version` from the checkout. The flow is left empty, so the entry
uses the defaults. `repo` is the folder name when the repos tree holds it, else the absolute
path.

- `exit 3` — this checkout is already mapped by the printed entry → resolve by that
  project name; never add a second entry.

### Edit

```bash
<base directory>/scripts/repo-map.sh add "New Client - Phase 1" newclient \
  --odoo-version 18.0 --branch-flow "task,UAT,main" \
  --release-assignee alice --release-reviewer bob
<base directory>/scripts/repo-map.sh set "New Client - Phase 1" --odoo-version 19.0
<base directory>/scripts/repo-map.sh set "New Client - Phase 1" --aliases "New Client - Support,New Client - Care"   # "" removes them
<base directory>/scripts/repo-map.sh set "New Client - Phase 1" --upgrade-branch 19.0=19-upgrade   # repeatable; 19.0= removes that series
<base directory>/scripts/repo-map.sh set-flow "New Client - Phase 1" "task,UAT,main"
<base directory>/scripts/repo-map.sh set-release-owners "New Client - Phase 1" alice bob
<base directory>/scripts/repo-map.sh add "Client - Support" /mnt/extra-addons --odoo-version 18.0
```

- `add` only creates and refuses a duplicate. It does not check that the repo folder exists.
  `set` keeps the fields you do not name.
- Never `remove` then re-`add` to change a field.
- One entry per customer repo. A second Odoo project on the same repo and chain is an
  alias, not an entry. `set`, `set-flow`, `set-release-owners` and `remove` take the
  entry name or, for `set*`, an alias.
- The CLI writes atomically and keeps one `.bak`.

## Rules

- An unknown project is a stop, never a guess. Bootstrap first, then stop on what remains.
- An entry is added only with explicit user confirmation of repo, series and chain.
- One repo with several projects is normal. Those entries can disagree on
  `odoo_version` and `branch_flow`. Name the project.
- Never hand-edit the map file.
- `base_branch` is what a task PR targets. It is often not the GitHub default branch.
