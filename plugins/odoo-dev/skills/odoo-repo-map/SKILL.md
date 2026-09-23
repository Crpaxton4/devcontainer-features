---
name: odoo-repo-map
description: "Resolve an Odoo project to its git repo, base branch, Odoo series, GitHub remote, and environment chain (dev → staging → production). Use before cutting a worktree, opening a PR, or planning a release, and when a project is added or renamed."
user-invocable: false
---
# Odoo Repo Map

One place answer "this Odoo project — which repo, which branch, which version, what come after this branch?" Rest of delivery suite (`odoo-dev:odoo-task-env`, `odoo-dev:odoo-pr`, `odoo-dev:odoo-release`, `odoo-dev:odoo-prior-art`) resolve through this skill, not infer from checkout.

This skill exist to stop inference. GitHub default branch not branch task PR target. Folder name not project name. Environment chain live nowhere in git.

Live values, injected every time this skill is rendered — at invocation and at every
agent spawn that preloads it. Trust them; do not re-derive them.

- **Projects in the map**: !`bash ${CLAUDE_SKILL_DIR}/scripts/repo-map.sh list 2>/dev/null | sed -n 's/^  "\(.*\)": {$/"\1"/p' | paste -sd ' ' - | grep . || echo "map unreadable — read it with repo-map.sh before you answer, and never guess"`

A project that is not named on that line is unmapped. Ask the user for its repo,
base branch, series and chain, or add it — never infer any of them from a folder
name or from a checkout.

## When to use

Before cutting a worktree, opening a pull request, or planning a release, and any time the repo, base branch, Odoo series, GitHub remote, or environment chain for a project is not already known; a new client project has to be added to the map; a repo was renamed or a branch flow is wrong; or someone asks which projects and repos are known.

## The data

`$ODOO_DEV_STATE_DIR/repo-map.json` (default `~/.local/share/odoo-dev/repo-map.json`) —
mutable state, deliberately outside the plugin tree so a plugin update never touches it.
`REPO_MAP_FILE` overrides the whole path. The in-tree `repo-map.json.seed` is an empty
skeleton `bootstrap-state.sh` copies in when the state file is absent, never a live map.
One entry per **exact Odoo `project.project` name**:

| Key | Meaning |
|-----|---------|
| `repo` | Bare folder name under repos tree — never path |
| `repo_path` | Optional **absolute** path to checkout. Overrides `$REPOS_DIR/$repo` |
| `default_branch` | Branch task work based on, and task PRs target. Must appear in `branch_flow`. Never `:task` |
| `odoo_version` | Series this project run, e.g. `18.0` |
| `branch_flow` | Ordered environment chain, **last element is production**, e.g. `[":task","UAT","main"]` |
| `flow_confirmed` | `true` only when human vouched for chain (see below) |
| `remote` | `owner/repo` on GitHub, when differ from what local clone show |
| `notes` | Free text for humans. Never parsed |
| `release_assignee` | GitHub login an aggregation release PR is assigned to |
| `release_reviewer` | GitHub login the release PR requests review from, or `none` |

**`repo_path` exists because the flat repos tree is convention, not law.** `repos-dir.sh` need directory whose immediate subdirectory names equal `repo`. Bind mount cannot supply one: Odoo devcontainer mount client repo at `/mnt/extra-addons`, name fixed by addons path, never match `repo`, and no `REPOS_DIR` value bridge that — `REPOS_DIR=/mnt` still leave folder named `extra-addons`. Record absolute path on entry instead. Project carrying `repo_path` never consult `repos-dir.sh` at all: `add` check that path exist, `project-resolve.sh` report it as `repo_path` and sniff `origin` from it, so `remote` fill in instead of stay null. Relative path rejected — resolve against whatever directory caller stand in, exactly guesswork this map exist to stop. `repo` stay required and stay bare folder name: it the lookup key, independent of where checkout mounted.

**`.odoo-repos-dir` marker exist because host with one clone cannot be inferred.** Sweep count >= 2 git subdirs because `$PWD` is first candidate: accept one and parent of any single checkout start masquerading as the tree, false positive far harder to notice than the false negative. Machine holding exactly one repo therefore declare tree once — `touch <tree>/.odoo-repos-dir` — and marker outrank the count, same authority as `REPOS_DIR`, which script never count either. Marker also resolve tree still empty, state fresh host clone into. Setting `REPOS_DIR` stay equally valid, just per-shell rather than per-machine.

**`release_assignee` / `release_reviewer` exist so the release route never has to ask.** Who owns a release PR is a property of the project, not of the run, and `odoo-dev:odoo-release` runs inside a subagent that cannot put a question to the user. Recorded once, read every release. Absent = unanswered: ask the user and record the answer, never infer one from commit authorship.

**`:task` is reserved first element of `branch_flow`, standing for per-task branch.** Chain's first element is where task work start from, and on most project that not shared branch at all — task branch cut fresh per unit of work, exist on no remote. Writing ordinary name there (old `dev` convention) claim branch every consumer walking chain believe in and none can resolve; `gh api repos/<owner>/<repo>/branches/dev` return 404 on every mapped remote. Record `:task` instead. Colon forbidden anywhere in git ref name, so token can never collide with branch anyone could create, and chain readable without guessing at index 0.

Token **optional**: project whose task work based directly on shared environment record that branch first and no placeholder — `["staging","main"]` with `default_branch: staging` correct and warn nothing. Rules validator enforce: placeholder only ever element 0, appear once, never `default_branch` (task PR need real branch to target), and every element after it must be legal git branch name that exist on remote. `validate` also **warn** (not fail) when chain start with name that neither `:task` nor `default_branch` — that the phantom, and warning carry `set-flow` command that repair it.

**`flow_confirmed` is honesty flag.** Seeded flows parsed out of old free-text notes written for people. Parsed chain = hypothesis. `project-resolve.sh` answer mid-chain hop from unconfirmed flow — wrong there cost re-run — but refuse to name **production** hop until someone confirm. Hit that refusal: ask user confirm chain, record it:

```bash
<base directory>/scripts/repo-map.sh set-flow "QOC Delivery Improvements" ":task,UAT,main" --flow-confirmed
```

## Scripts

This skill's scripts (`MAP_SCRIPTS`) live at `<base directory>/scripts`, where
`<base directory>` is the absolute path on the `Base directory for this skill:`
line injected above this body. The name is a label for that directory, not a shell
variable to set and reuse: every Bash call has to spell the absolute path out in
full, because a Bash call inherits no environment and keeps no state from the call
before it.

`${CLAUDE_SKILL_DIR}` in the injected bullet above is not a shell variable either.
The renderer substitutes it into this body before any shell sees it, and a Bash tool
call is not rendered, so the same spelling expands to nothing in one of your own
calls. Spell the base directory out in full there.

Every script print one JSON object as last stdout line.

| Script | Use |
|--------|-----|
| `repos-dir.sh [--raw]` | Resolve repos tree → `{"repos_dir"}`. Honours `$REPOS_DIR` first, then sweep `$PWD` + candidates for a dir carrying `.odoo-repos-dir` marker or >= 2 git subdirs. Exit 1 = no tree here, not fatal — entries with `repo_path` skip it |
| `repo-map.sh get\|list\|add\|set\|set-flow\|set-release-owners\|remove\|validate` | **Only** sanctioned way to read or edit map |
| `project-resolve.sh "<project\|repo>" [--next-after <branch>]` | Full resolution, plus next environment in chain |

### Resolving

```bash
<base directory>/scripts/project-resolve.sh "B&K Logistics Support"
<base directory>/scripts/project-resolve.sh "B&K Logistics Support" --next-after :task  # first hop -> next_env: "UAT"
<base directory>/scripts/project-resolve.sh "B&K Logistics Support" --next-after UAT    # -> next_env: "main"
<base directory>/scripts/project-resolve.sh B-K-Logistics                               # repo folder also works
```

`--next-after` take **element of chain**, never arbitrary branch. First hop — out of per-task branch, into whatever that task PR target — spelled `--next-after :task`, same arithmetic as every other hop. Real branch name not on chain still exit 4: treating unrecognised name as "must be task branch" would answer typo'd environment name with `default_branch`.

Output: `{"project","repo","repo_path","default_branch","odoo_version","branch_flow","flow_confirmed","remote","next_env","at_production","release_assignee","release_reviewer"}`.

The two `release_*` fields are `null` when the project recorded none.

`repo_path` is checkout path when one present on this machine, `null` when none — metadata still correct, still useful for planning. Only filesystem-touching skills need path. Entry's own `repo_path` win over `$REPOS_DIR/$repo`, so project pinned that way resolve even where no repos tree exist. Hand value straight to `odoo-dev:odoo-task-env` scripts' `--repo-path DIR` — same bypass, now recorded once instead of typed every call.

Exit codes: `2` usage · `3` unmapped **or** repo folder mapping to several projects · `4` branch chain cannot answer question asked.

### Editing

```bash
<base directory>/scripts/repo-map.sh add "New Client - Phase 1" newclient \
  --default-branch UAT --odoo-version 18.0 \
  --branch-flow ":task,UAT,main" --flow-confirmed \
  --remote acme-eng/newclient --notes "..." \
  --release-assignee alice --release-reviewer bob
```

`add` only create, and refuse duplicate. Already-mapped project need setter. `set` merge into existing entry — field you not name keep its value:

```bash
<base directory>/scripts/repo-map.sh set "New Client - Phase 1" \
  --remote acme-eng/newclient --notes "release goes out Thursdays" \
  --default-branch UAT --odoo-version 18.0 --repo-path /mnt/extra-addons
```

**Never `remove` then re-`add` to change a field.** That the old way and it lossy — every field you leave off the re-`add` silently dropped, `notes` first — and non-atomic: `remove` commit its own write before `add` run, so failed `add` leave project simply gone, `.bak` holding only post-`remove` state. `set` write once, through same validate → `.bak` → rename path.

Empty value delete the key: `set "<project>" --repo-path ""` put entry back on flat tree. Only way a merge can say "unset this". `--repo-path` on `set` check same as on `add` — absolute, and directory must exist unless `--no-repo-check`.

Two fields `set` deliberately not take, because each carry own extra question — it point you at owner instead:

```bash
<base directory>/scripts/repo-map.sh set-flow "New Client - Phase 1" ":task,UAT,main" --flow-confirmed
<base directory>/scripts/repo-map.sh set-release-owners "New Client - Phase 1" alice bob
```

Checkout not under repos tree (bind mount, devcontainer) — pin it:

```bash
<base directory>/scripts/repo-map.sh add "Client - Support" clientrepo \
  --repo-path /mnt/extra-addons --default-branch staging --odoo-version 18.0
```

Writes atomic (temp file → validate → `.bak` → rename), so rejected edit never become live map. `add` check repo folder exist unless you pass `--no-repo-check` — checks `--repo-path` when given, else `$REPOS_DIR/$repo`.

## Rules

- **Unknown project = stop, not guess.** `project-resolve.sh` exit 3, list what it know. Ask user which repo and branch, then `add` entry. Never infer repo from similar name.
- **Entries added only with explicit user confirmation** — repo, base branch, series, chain.
- **One repo, several projects is normal** (delivery project and its upgrade project share checkout). Those entries can legitimately disagree on `odoo_version` and `branch_flow` — that why resolving by folder name ambiguous by design. Name the project.
- **Never hand-edit the map file.** Validator enforce things easy to get wrong by hand: `default_branch` on chain and never `:task`, no repeats, no unknown keys, `repo_path` absolute, `:task` only element 0, every other `branch_flow` element a legal git branch name.
- **Never invent branch name for chain's first element.** Task branch cut fresh per task is `:task`, not `dev`. Only thing that go on chain is branch that exist on remote, plus that one placeholder.
- `default_branch` is what task PR target. Frequently **not** GitHub default branch. Real case: client repo whose GitHub default is `Odoov18` while every task PR belongs on `staging`.

## Verify

```bash
bash <base directory>/scripts/tests/repo-map.test.sh    # offline; no network, no repos tree, no Odoo
bash <base directory>/scripts/tests/repos-dir.test.sh  # offline; resolver only — explicit REPOS_DIR, sweep, marker
```