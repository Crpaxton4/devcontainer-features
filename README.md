# Personal Dev Container Features

This repo holds a devcontainer [Features](https://containers.dev/implementors/features/) collection (`personal-features`, `second-brain`), a Python SDK for Odoo ERP access (`odoo_sdk`), and a Claude Code plugin for Odoo consulting and delivery (`odoo-dev`).

This is the only `README.md` in the repo. Per-Feature documentation lives in `devcontainer-features/src/<feature>/NOTES.md`, and the plugin's skills, agents and commands document themselves in their own files.

- [`personal-features`](#personal-features)
- [`odoo_sdk`](#odoo_sdk)
- [odoo-dev plugin](#odoo-dev-plugin)
- [Repo and Feature structure](#repo-and-feature-structure)
- [Versioning & releases](#versioning--releases)
- [Testing](#testing)

## `personal-features`

Personal dev container tooling, meant to be added as a default Feature across projects. Currently it installs [Claude Code](https://code.claude.com/docs) and wraps the `claude` command so a default session automatically connects to the IDE (`--ide`), since this Feature is meant purely for use inside a VS Code dev container. It also persists Claude Code's and the GitHub CLI's auth/config by bind-mounting them from your host home directory, shared across every project on the machine, so logging in once is enough.

It pairs with the official Node.js and GitHub CLI Features, which it doesn't reimplement.

```jsonc
{
    "image": "mcr.microsoft.com/devcontainers/base:ubuntu",
    "features": {
        "ghcr.io/devcontainers/features/node:1": {
            "version": "lts"
        },
        "ghcr.io/devcontainers/features/github-cli:1": {},
        "ghcr.io/<owner>/<repo>/personal-features:1": {}
    }
}
```

```bash
$ claude
# behaves like `claude --ide`

$ claude mcp
# subcommands are passed through untouched
```

### One-time host setup

The Feature bind-mounts config from your host home directory, and a bind mount whose source doesn't exist is a hard container-create failure. Create the paths once per machine:

```sh
./setup.sh      # Linux, WSL, macOS
```

```powershell
.\setup.ps1     # native Windows host
```

Run this before `devcontainer features test` too, or the test containers fail to start on the missing mount sources.

### What persists, and where

| Host path | Mounted at | Used for |
| --- | --- | --- |
| `~/.claude` | `/usr/local/share/claude-home` | `CLAUDE_CONFIG_DIR` — Claude Code's auth (`.credentials.json`) and settings. |
| `~/.config/gh` | `/usr/local/share/gh-cli-config` | `GH_CONFIG_DIR` — so `gh auth login` happens once per machine. |
| `~/.config/odoo_sdk` | `/usr/local/share/odoo-sdk-config` | `ODOO_SDK_CONFIG` — points at the dir; the SDK probes it for `config.toml`/`config.ini`. |
| `~/.config/pr-automation` | `/usr/local/share/pr-automation` | `PR_AUTOMATION_CONFIG_DIR` — `create-pr`'s global and per-project config. |
| `~/.config/coderabbit` | `/usr/local/share/coderabbit-config` | `CODERABBIT_CONFIG_DIR` — CodeRabbit CLI config/auth state. |
| `~/.config/odoo-dev` | `/usr/local/share/odoo-dev` | `ODOO_DEV_STATE_DIR` — the [odoo-dev plugin](#odoo-dev-plugin)'s repo map, upgrade lessons and handoff artifacts. |
| `~/.config/devcontainer/shell-history` | `/usr/local/share/shell-history` | Bash history, shared across containers and projects. |

See [`devcontainer-features/src/personal-features/NOTES.md`](devcontainer-features/src/personal-features/NOTES.md) for more detail, including the Windows and WSL notes.

## `odoo_sdk`

A Python SDK for Odoo ERP access via XML-RPC and JSON-RPC, with a built-in [MCP](https://modelcontextprotocol.io) server so AI agents can call Odoo operations as tools.

### Subsystems

The complete map of `src/odoo_sdk/` — detail lives in the Sphinx docs.

| Package | Role |
| --- | --- |
| `client` | `OdooClient` — connects to an Odoo server and returns model proxies via `client["model.name"]`; reads settings from keyword args or a `.odoo_sdk.ini` file. |
| `records` | `OdooRecordset` — an ordered set of records; supports `search`, `read`, `write`, `create`, `unlink`, and x2many field commands. |
| `query` | `DomainExpression` — composes search domains from `Condition` nodes (`&`, `\|`, `!`) and serializes to the wire format. |
| `fields` | Field-value adaptation and x2many command normalization. |
| `commands` | `Command` / `Registry` — Odoo operations wrapped as named, dependency-injected commands; the `Registry` owns each command's shared client/state/config. |
| `mcp` | `OdooMCPServer` — FastMCP server exposing an explicit, hand-written tool set (`build_explicit_tools`) over a `Registry`, with no auto-reflection of command signatures. |
| `cli` | Command-line companion for Odoo task time-tracking. |
| `tui` | Terminal UI that explores sessions over a date window as per-lane timeline bars. |
| `transport` | XML-RPC / JSON-RPC transport and the SDK's error hierarchy. |
| `env` | Metadata cache for Odoo model schema. |
| `state` | `LocalStateClient` (SQLite task-session FSM) and `LocalConfig` (File > Env > Default settings). |
| `sessionization` | Pure Transform + Load core that derives sessions from tracked events; unaware of SQLite, git, and MCP. |
| `adapters` | Bridge the pure `sessionization` core to stateful edges — the SQLite `events` table and external systems. |
| `billing` | Turns tracked work into billed Odoo timesheet rows (`account.analytic.line`). |
| `utilities` | Reusable pure helpers and thin single-call Odoo wrappers that commands compose. |

### Quickstart

```python
from odoo_sdk import OdooClient

client = OdooClient(url="https://myodoo.example.com", db="mydb", username="admin", password="...")
tasks = client["project.task"].search([("stage_id.name", "=", "In Progress")], limit=20).read(["name", "user_ids"])
```

Connection settings can also be stored in `.odoo_sdk.ini`:

```ini
[odoo]
url = https://myodoo.example.com
db  = mydb
username = admin
password = ...
```

### MCP server

Build a `Registry`, turn its commands into an explicit tool set, and serve them:

```python
from odoo_sdk import OdooClient, OdooMCPServer, Registry
from odoo_sdk.commands.builtin import register_builtins
from odoo_sdk.mcp.tools import build_explicit_tools

# Register the SDK's built-in commands, then build the explicit, typed tool
# set that wraps them. The server exposes exactly these tools — it does not
# auto-reflect command signatures.
registry = register_builtins(Registry(OdooClient()))
server = OdooMCPServer(registry, explicit_tools=build_explicit_tools(registry))
server.run()
```

Or run the packaged entry point directly:

```bash
odoo-mcp
```

### Setup

```bash
cd libraries/odoo_sdk
uv sync           # install deps + dev groups
uv run python -m unittest discover -s tests -t .   # run tests
make coverage     # run tests + enforce 90 % coverage threshold
```

## odoo-dev plugin

Odoo consulting and delivery packaged as one Claude Code plugin, at [`plugins/odoo-dev/`](plugins/odoo-dev). The repo root [`.claude-plugin/marketplace.json`](.claude-plugin/marketplace.json) is the marketplace that serves it — there is no server and no registry, so the repo is the whole install path.

```bash
claude plugin marketplace add Crpaxton4/devcontainer-features
claude plugin install odoo-dev@devcontainer-features
claude plugin update  odoo-dev    # version-triggered: refetches only when plugin.json's `version` moved
```

The repo is private, so both go through your existing `gh`/git credentials. For a fast edit loop, a checkout at `<config>/skills/odoo-dev/` loads in place as `odoo-dev@skills-dir` — never alongside an installed copy, or every skill, agent and command registers twice and competes for the same triggers.

Start at [`odoo-dev:odoo-dev-map`](plugins/odoo-dev/skills/odoo-dev-map/SKILL.md). It routes work to the skill or agent that owns it and never does the stage work itself. What follows is a map; the `SKILL.md` and agent files are the territory.

### Inventory

**16 skills, 5 subagents, 5 slash commands** — the counts [`plugins/odoo-dev/scripts/validate.sh`](plugins/odoo-dev/scripts/validate.sh) asserts. Reference a skill as `odoo-dev:<name>`; frontmatter names stay bare and the namespace is derived. Individual plugin skills cannot be switched off — `skillOverrides` never reaches a plugin-sourced skill — so the only granularity is the whole plugin or none of it.

| Group | Skills (under [`plugins/odoo-dev/skills/`](plugins/odoo-dev/skills)) |
| --- | --- |
| Delivery — the doer chain, one step per skill | `odoo-repo-map`, `odoo-prior-art`, `odoo-task-env`, `odoo-test-run`, `odoo-code-review`, `odoo-pr`, `odoo-release` |
| Consulting | `discovery-notes`, `odoo-quote`, `fibonacci-estimate`, `odoo-design-doc` |
| Platform | `odoo-devcontainer`, `odoo-populate-db`, `odoo-upgrade`, `principles`, `odoo-dev-map` |

Agents are plain subagents, in [`plugins/odoo-dev/agents/`](plugins/odoo-dev/agents). Their names carry the `odoo-dev-` prefix because plugin agent names are **not** auto-namespaced and would otherwise collide across plugins.

| Agent | Owns | Writes |
| --- | --- | --- |
| [`odoo-dev-scoper`](plugins/odoo-dev/agents/odoo-dev-scoper.md) | Discovery, prior-art verdict, estimate, design doc. Never touches a repo | `05-scope.json` |
| [`odoo-dev-builder`](plugins/odoo-dev/agents/odoo-dev-builder.md) | One task, one worktree: code, tests, conventional commits | `10-env.json`, `20-build.json` |
| [`odoo-dev-tester`](plugins/odoo-dev/agents/odoo-dev-tester.md) | Independent evidence: tests, tours, the Odoo review lens. **Cannot edit code** | `30-test.json`, `35-review.json` |
| [`odoo-dev-pr`](plugins/odoo-dev/agents/odoo-dev-pr.md) | Push, local CodeRabbit review, draft PR, promotion, chatter notes | `40-coderabbit.json`, `50-pr.json`, `60-release.json` |
| [`odoo-dev-upgrader`](plugins/odoo-dev/agents/odoo-dev-upgrader.md) | Cross-version porting, 16 → 17 → 18 → 19 | `10-env.json`, `20-build.json` |

One slash command per agent, in [`plugins/odoo-dev/commands/`](plugins/odoo-dev/commands): `/odoo-dev:quote`, `/odoo-dev:task`, `/odoo-dev:upgrade`, `/odoo-dev:test`, and `/odoo-dev:pr` (which also carries the release route, `/odoo-dev:pr release <from> <to>`). Each resolves the paths its agent needs and dispatches it. **No command chains to another** — you type the next one once you have read what the last one returned.

### Dispatch

Routing ends in a `Task` call, not in a recommendation: naming the owning agent in prose dispatches nothing. `subagent_type` is the namespaced name (`odoo-dev:odoo-dev-builder`, and the same shape for the other four) — a bare name does not resolve, and the fallback to `general-purpose` drops every `skills:` preload, `disallowedTools` entry and hook the definition carries.

Every spawn prompt carries two absolute paths, typed out in full in every call: the **artifacts directory** and [`scripts/artifact.sh`](plugins/odoo-dev/scripts/artifact.sh). A subagent's Bash call inherits no environment and keeps no state from the call before it, so a variable name in a command is not a path — it expands to nothing and the command runs without it. There is no evidence gate in front of any of this: the artifacts are evidence an agent reads and reports on, never a precondition that blocks the work.

| Workflow | Chain |
| --- | --- |
| Scoping and quoting | `scoper` → `05-scope.json` → `builder` |
| Task delivery | `builder` → `tester` → `pr` |
| Version upgrade | `upgrader` → `tester` → `pr` |
| Release | `pr` → draft release PR + a note on every included task |

### Handoff artifacts

Append-only JSON in a per-task directory, written only through `artifact.sh` — artifacts survive compaction, a session boundary, a killed subagent, and a human taking over mid-chain, and a JSON blob in a prompt survives none of those.

`00-context` · `05-scope` · `10-env` · `20-build` · `30-test` · `35-review` · `40-coderabbit` · `50-pr` · `60-release`

`artifact.sh` validates required fields and cheap types before the file is named, writes atomically, and **never overwrites**: a second put of a stage lands at `<stage>.2.json`, and `get` reads the latest revision while `list` shows them all — so a chain can recover from a red test, but "green on the third try" can never read as "green".

### Hooks

Two `PreToolUse` hooks on `Bash`, declared in [`hooks/hooks.json`](plugins/odoo-dev/hooks/hooks.json) and shipped inside the plugin. Nothing is written to your `settings.json`; enabling or disabling the plugin turns them on and off with everything else, and both stay silent unless they have something to say.

| Hook | Fires on | Denies |
| --- | --- | --- |
| [`bash-allowlist.sh`](plugins/odoo-dev/hooks/bash-allowlist.sh) | Every `Bash` call whose payload reports `agent_type` `odoo-dev-tester` | Anything off a short allowlist of sanctioned scripts and read-only `git`. This is what makes "cannot edit code" a property of the harness rather than a promise in a prompt; `disallowedTools` never covered `Bash` |
| [`commit-hook.sh`](plugins/odoo-dev/hooks/commit-hook.sh) | `git commit`, from any agent | A commit carrying changes to a module whose `__manifest__.py` `version` has not moved since the base commit. Everything it cannot attribute — no module, no repo, a merge in progress, an explicit pathspec — passes in silence |

### State

Mutable state lives **outside** the plugin tree, at `${ODOO_DEV_STATE_DIR:-$HOME/.local/share/odoo-dev}`: `repo-map.json`, `upgrade-lessons/`, and `tasks/<task_id>/`. Inside a container `personal-features` sets `ODOO_DEV_STATE_DIR` to `/usr/local/share/odoo-dev` and bind-mounts host `~/.config/odoo-dev` there, so state outlives a rebuild — the `$HOME` default is what a bare checkout gets, and inside a container it resolves to image storage a rebuild discards. [`scripts/state-dir.sh`](plugins/odoo-dev/scripts/state-dir.sh) is the single definition of that rule; nothing else may hard-code the default. Seed the dir with `plugins/odoo-dev/scripts/bootstrap-state.sh`, which is idempotent and seeds only what is absent.

### Verify

```bash
plugins/odoo-dev/scripts/setup.sh --check   # is this machine ready? report only, non-zero if not
plugins/odoo-dev/scripts/validate.sh        # every gate in one call; offline
```

`validate.sh` covers the manifest and component inventory, frontmatter and body limits, router completeness, agent definitions, namespacing, hard-coded paths, stray skills, eval-suite structure, the offline script test suites, shell syntax, and release-version drift. [`.github/workflows/plugin-odoo-dev.yaml`](.github/workflows/plugin-odoo-dev.yaml) runs it on CI, paths-filtered to the plugin, alongside the per-skill script suites and a pinned shellcheck; it sets `REQUIRE_CLAUDE=1` so the one skippable gate (`claude plugin validate`) becomes a hard failure there, and a green CI run is never one where that gate quietly did not happen.

release-please treats `plugins/odoo-dev` as its own package (component `odoo-dev-plugin`) and bumps `version` in `plugins/odoo-dev/.claude-plugin/plugin.json`, which is the only thing `claude plugin update` reacts to — a merge that does not move that string ships nothing to anyone.

## Repo and Feature structure

The two packages sit under `devcontainer-features/` and `libraries/`, the plugin under `plugins/`, with shared scripts in `scripts/`:

```
├── devcontainer-features
│   ├── src/personal-features/     # devcontainer Feature — install.sh, hooks/, create-pr/, claude-event-hook/, sync-claude-*, NOTES.md
│   ├── src/second-brain/          # devcontainer Feature — bind-mounts the host knowledge base, NOTES.md
│   └── test/                      # feature test scenarios, one dir per Feature
├── libraries
│   └── odoo_sdk/                  # owns src/, tests/, docs/, examples/, tools/ directly
│       ├── src/odoo_sdk/          # adapters, billing, cli, client, commands, env, fields, mcp, query, records, sessionization, state, transport, tui, utilities
│       ├── tests/                 # one test_<pkg>/ per subpackage
│       ├── docs/source/           # Sphinx docs
│       ├── examples/, tools/
│       └── pyproject.toml, Makefile
├── plugins
│   └── odoo-dev/                  # Claude Code plugin — skills/, agents/, commands/, hooks/, scripts/, evals/
└── scripts/                       # google_oauth_setup.py, init_tracker_db.py
```

Feature documentation lives in `devcontainer-features/src/<feature>/NOTES.md`, next to the code it describes. Nothing generates a README from it, and this file at the root is the repo's only `README.md`.

## Versioning & releases

Commit messages and PR titles follow [Conventional Commits](https://www.conventionalcommits.org/), enforced two ways:

- A local Husky `commit-msg` hook (via commitlint) checks every commit.
- A CI check, **Lint PR Title** (`.github/workflows/pr-title-lint.yaml`), checks the PR title and is a required status check on `main` — this repo only allows squash-merge, so the PR title (not the individual commit messages) is what actually lands on `main`.

[release-please](https://github.com/googleapis/release-please) watches `main` for conventional commits touching `src/personal-features` and opens/updates a release PR that bumps `version` in `src/personal-features/devcontainer-feature.json` and updates its `CHANGELOG.md`. Merging that PR cuts a GitHub Release, which automatically triggers the GHCR publish workflow (`.github/workflows/release.yaml`) — no manual `workflow_dispatch` needed, though that trigger is still available if you need to re-publish by hand.

### Publishing

Features are published to GHCR by `.github/workflows/release.yaml`, namespaced as `ghcr.io/<owner>/<repo>/<feature-id>:<version>`. *Allow GitHub Actions to create and approve pull requests* needs to be enabled in `Settings > Actions > General > Workflow permissions` for release-please's release PRs. The workflow publishes only: it generates no documentation and opens no docs PR.

GHCR packages default to `private`. To use a Feature across projects without per-repo tokens, mark its package `public` from the package's GHCR settings page.

## Testing

### `personal-features`

Tests use the `devcontainer features test` command from `@devcontainers/cli` and the `dev-container-features-test-lib` helper. Install the CLI with:

```bash
npm install -g @devcontainers/cli
```

Run from the repo root:

```bash
# Autogenerated (default-options) test — personal-features needs a Node-enabled base image
devcontainer features test -p ./devcontainer-features --skip-scenarios -f personal-features -i mcr.microsoft.com/devcontainers/javascript-node:latest .

# Scenario test (combines personal-features with the official node + github-cli Features)
devcontainer features test -p ./devcontainer-features -f personal-features --skip-autogenerated --skip-duplicated .
```

### `odoo_sdk`

```bash
cd libraries/odoo_sdk
uv sync                      # install all dependency groups

# Unit tests
uv run python -m unittest discover -s tests -p "test_*.py" -t .

# Coverage (enforces 90 % threshold)
make coverage

# Static analysis
make quality
```
