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
| `default_branch` | Branch task work based on, and task PRs target. Must appear in `branch_flow` |
| `odoo_version` | Series this project run, e.g. `18.0` |
| `branch_flow` | Ordered environment chain, **last element is production**, e.g. `["dev","UAT","main"]` |
| `flow_confirmed` | `true` only when human vouched for chain (see below) |
| `remote` | `owner/repo` on GitHub, when differ from what local clone show |
| `notes` | Free text for humans. Never parsed |
| `release_assignee` | GitHub login an aggregation release PR is assigned to |
| `release_reviewer` | GitHub login the release PR requests review from, or `none` |

**`release_assignee` / `release_reviewer` exist so the release route never has to ask.** Who owns a release PR is a property of the project, not of the run, and `odoo-dev:odoo-release` runs inside a subagent that cannot put a question to the user. Recorded once, read every release. Absent = unanswered: ask the user and record the answer, never infer one from commit authorship.

**`flow_confirmed` is honesty flag.** Seeded flows parsed out of old free-text notes written for people. Parsed chain = hypothesis. `project-resolve.sh` answer mid-chain hop from unconfirmed flow — wrong there cost re-run — but refuse to name **production** hop until someone confirm. Hit that refusal: ask user confirm chain, record it:

```bash
<base directory>/scripts/repo-map.sh set-flow "QOC Delivery Improvements" "dev,UAT,main" --flow-confirmed
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
| `repos-dir.sh [--raw]` | Resolve repos tree → `{"repos_dir"}`. Honours `$REPOS_DIR` first |
| `repo-map.sh get\|list\|add\|set-flow\|remove\|validate` | **Only** sanctioned way to read or edit map |
| `project-resolve.sh "<project\|repo>" [--next-after <branch>]` | Full resolution, plus next environment in chain |

### Resolving

```bash
<base directory>/scripts/project-resolve.sh "B&K Logistics Support"
<base directory>/scripts/project-resolve.sh "B&K Logistics Support" --next-after dev   # -> next_env: "UAT"
<base directory>/scripts/project-resolve.sh B-K-Logistics                             # repo folder also works
```

Output: `{"project","repo","repo_path","default_branch","odoo_version","branch_flow","flow_confirmed","remote","next_env","at_production","release_assignee","release_reviewer"}`.

The two `release_*` fields are `null` when the project recorded none.

`repo_path` is `null` when repos tree not on this machine — metadata still correct, still useful for planning. Only filesystem-touching skills need path.

Exit codes: `2` usage · `3` unmapped **or** repo folder mapping to several projects · `4` branch chain cannot answer question asked.

### Editing

```bash
<base directory>/scripts/repo-map.sh add "New Client - Phase 1" newclient \
  --default-branch UAT --odoo-version 18.0 \
  --branch-flow "dev,UAT,main" --flow-confirmed \
  --remote acme-eng/newclient --notes "..." \
  --release-assignee alice --release-reviewer bob
```

`add` only create. Already-mapped project need setter:

```bash
<base directory>/scripts/repo-map.sh set-release-owners "New Client - Phase 1" alice bob
```

Writes atomic (temp file → validate → `.bak` → rename), so rejected edit never become live map. `add` check repo folder exist unless you pass `--no-repo-check`.

## Rules

- **Unknown project = stop, not guess.** `project-resolve.sh` exit 3, list what it know. Ask user which repo and branch, then `add` entry. Never infer repo from similar name.
- **Entries added only with explicit user confirmation** — repo, base branch, series, chain.
- **One repo, several projects is normal** (delivery project and its upgrade project share checkout). Those entries can legitimately disagree on `odoo_version` and `branch_flow` — that why resolving by folder name ambiguous by design. Name the project.
- **Never hand-edit the map file.** Validator enforce things easy to get wrong by hand: `default_branch` on chain, no repeats, no unknown keys.
- `default_branch` is what task PR target. Frequently **not** GitHub default branch. Real case: client repo whose GitHub default is `Odoov18` while every task PR belongs on `staging`.

## Verify

```bash
bash <base directory>/scripts/tests/repo-map.test.sh   # offline; no network, no repos tree, no Odoo
```