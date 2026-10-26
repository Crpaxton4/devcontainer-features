---
name: odoo-repo-map
description: "Resolve an Odoo project to its git repo, base branch, Odoo series, GitHub remote, and environment chain (dev → staging → production). Use before cutting a worktree, opening a PR, or planning a release, and when a project is added or renamed."
user-invocable: false
---
# Odoo Repo Map

One answer to "this Odoo project: which repo, which branch, which series, what comes after
this branch?". Nothing is inferred from a checkout: the GitHub default branch is not the
base, the folder name is not the project name, and the chain lives nowhere in git.

## The data

`$ODOO_DEV_STATE_DIR/repo-map.json`. Containers always have `ODOO_DEV_STATE_DIR` set. On a
host where it is unset, the default is `~/.config/odoo-dev` when that directory exists (the
directory the containers bind), else `~/.local/share/odoo-dev`.
`REPO_MAP_FILE` overrides the path. A map that exists but does not parse or validate
exits 4 naming the file and the complaint. It is never reseeded.

One entry per exact Odoo `project.project` name:

| Key | Meaning |
|-----|---------|
| `repo` | Bare folder name under the repos tree. Never a path |
| `repo_path` | Optional absolute path to the checkout. Overrides `$REPOS_DIR/$repo` |
| `default_branch` | The branch task work is based on and task PRs target. Must appear in `branch_flow`. Never `:task` |
| `odoo_version` | The series this project runs, e.g. `18.0` |
| `branch_flow` | Ordered environment chain, e.g. `[":task","UAT","main"]` |
| `flow_confirmed` | `true` only when a human vouched for the chain |
| `remote` | `owner/repo` on GitHub, when it differs from the local clone |
| `notes` | Free text for humans. Never parsed |
| `release_assignee` | GitHub login a release PR is assigned to |
| `release_reviewer` | GitHub login the release PR requests review from, or `none` |
| `aliases` | Optional array of other `project.project` names that resolve to this entry. Never an entry name. Each alias appears once in the map |
| `upgrade_branch` | Optional object of target series to branch, e.g. `{"19.0":"19-upgrade"}`. The branch an upgrade to that series is based on and its PR targets. A series with no key uses `default_branch` |

Five facts:

- `repo_path` is absolute. A relative path is rejected.
- A `.odoo-repos-dir` marker file declares a repos tree that holds one clone. It outranks
  the two-git-subdirectories sweep. `REPOS_DIR` is the per-shell equivalent.
- `:task` is only ever element 0 of `branch_flow`. It stands for the per-task branch and
  can never be `default_branch`.
- `flow_confirmed` becomes `true` only through `set-flow ... --flow-confirmed`, run by a
  person.
- The last element of `branch_flow` is production.

## Scripts

Every script prints one JSON object as its last stdout line. Write every path in full.

| Script and arguments | Prints | Exit |
|---|---|---|
| `repos-dir.sh [--raw]` | `{"repos_dir"}` from `$REPOS_DIR`, then the marker, then the sweep | 0 · 1 no tree (entries with `repo_path` skip it) |
| `repo-map.sh get\|list\|add\|set\|set-flow\|set-release-owners\|remove\|validate` | the only sanctioned way to read or edit the map | 0 · 2 usage · 3 unmapped · 4 map invalid |
| `project-resolve.sh "<project\|repo>" [--next-after <branch>] [--series NN.0]` | `{"project","repo","repo_path","default_branch","upgrade_branch","odoo_version","branch_flow","flow_confirmed","remote","next_env","at_production","release_assignee","release_reviewer","alias_of"}` (`alias_of` is the entry name when the key was an alias, else `null`; `project` is the key as typed; `upgrade_branch` is the entry's branch for `--series`, else `default_branch`, and `null` without `--series`) | 0 · 2 usage or a `--series` that is not `NN.0` · 3 unmapped · 4 chain cannot answer · 6 ambiguous |
| `project-bootstrap.sh "<checkout path>" [--project NAME]` | the entry it wrote: `repo`, `repo_path`, `remote`, `odoo_version`, `default_branch`; never `branch_flow` | 0 · 2 not a git checkout · 3 already pinned (entry printed) |

### Resolve

```bash
<base directory>/scripts/project-resolve.sh "B&K Logistics Support"
<base directory>/scripts/project-resolve.sh "B&K Logistics Support" --next-after :task   # -> next_env: "UAT"
```

`--next-after` takes an element of the chain, never an arbitrary branch. The first hop
is `--next-after :task`. `repo_path` is `null` when no checkout is on this machine. The
two `release_*` fields are `null` when the project recorded none.

- `exit 3` — unmapped → run `project-bootstrap.sh "<checkout>"`, then resolve again by
  the project name it printed; still 3 → stop, report the path and the output.
- `exit 4` — the chain cannot answer (unconfirmed or missing production hop) → stop;
  hand over the printed `set-flow` command verbatim; never run it yourself.
- `exit 6` — ambiguous: one repo folder maps to several projects → stop; list the
  candidate project names; never pick one.
- `exit 2` — usage → fix the call.

### Bootstrap an unmapped checkout

```bash
<base directory>/scripts/project-bootstrap.sh /mnt/extra-addons --project "ACME Support"
```

It records the mechanically derivable half of an entry and leaves `branch_flow` unset, so
`flow_confirmed` stays false. Bootstrapping is not confirming.

- `exit 3` — this checkout is already pinned by the printed entry → resolve by that
  project name; never add a second entry.

### Edit

```bash
<base directory>/scripts/repo-map.sh add "New Client - Phase 1" newclient \
  --default-branch UAT --odoo-version 18.0 \
  --branch-flow ":task,UAT,main" --flow-confirmed \
  --remote acme-eng/newclient --release-assignee alice --release-reviewer bob
<base directory>/scripts/repo-map.sh set "New Client - Phase 1" \
  --remote acme-eng/newclient --notes "release goes out Thursdays" --repo-path /mnt/extra-addons
<base directory>/scripts/repo-map.sh set "New Client - Phase 1" --aliases "New Client - Support,New Client - Care"   # "" removes them
<base directory>/scripts/repo-map.sh set "New Client - Phase 1" --upgrade-branch 19.0=19-upgrade   # repeatable; 19.0= removes that series
<base directory>/scripts/repo-map.sh set-flow "New Client - Phase 1" ":task,UAT,main" --flow-confirmed
<base directory>/scripts/repo-map.sh set-release-owners "New Client - Phase 1" alice bob
<base directory>/scripts/repo-map.sh add "Client - Support" clientrepo \
  --repo-path /mnt/extra-addons --default-branch staging --odoo-version 18.0
```

- `add` only creates and refuses a duplicate. `set` merges into an existing entry.
- Never `remove` then re-`add` to change a field. Every field left off the re-add is lost.
- An empty value deletes a key: `set "<project>" --repo-path ""`.
- `--repo-path` must be absolute and must exist, unless `--no-repo-check`.
- One entry per customer repo. A second Odoo project on the same repo and chain is an
  alias, not an entry. `add` also takes `--aliases "A,B"` and `--upgrade-branch SERIES=BRANCH`. `set`, `set-flow`,
  `set-release-owners` and `remove` given an alias exit 3: use the entry name.
- Writes are atomic: temp file → validate → `.bak` → rename.

## Rules

- An unknown project is a stop, never a guess. Bootstrap first, then stop on what remains.
- One checkout, one entry. Two entries may share `repo`, never `repo_path` with the same remote.
  A shared bind mount such as `/mnt/extra-addons` may hold one entry per remote. A `repo_path`
  counts only when the checkout's `origin` matches the entry's `remote` (or its `repo` name when
  there is no `remote`). Otherwise `project-resolve.sh` prints `repo_path: null` and a stderr line.
- An entry is added only with explicit user confirmation of repo, base, series and chain.
- One repo with several projects is normal. Those entries can disagree on
  `odoo_version` and `branch_flow`. Name the project.
- Never hand-edit the map file. The validator enforces what is easy to get wrong.
- `default_branch` is what a task PR targets. It is often not the GitHub default branch.
