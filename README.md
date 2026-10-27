# Personal Dev Container Features

This repo holds a devcontainer [Features](https://containers.dev/implementors/features/) collection (`personal-features`, `second-brain`, `debian-eol-archives`), a Python SDK for Odoo ERP access (`odoo_client`), a Claude Code plugin for Odoo consulting and delivery (`odoo-dev`), a host-side task dispatcher (`dispatcher`) and the Claude Code plugin that feeds it (`odoo-orchestrator`). The master design of the orchestrator stack is [`docs/design/devcontainer-task-orchestrator.html`](docs/design/devcontainer-task-orchestrator.html).

This is the only `README.md` in the repo. Per-Feature documentation lives in `devcontainer-features/src/<feature>/NOTES.md`, and the plugin's skills, agents and commands document themselves in their own files.

- [`personal-features`](#personal-features)
- [`odoo_client`](#odoo_client)
- [odoo-dev plugin](#odoo-dev-plugin)
- [odoo-orchestrator plugin](#odoo-orchestrator-plugin)
- [dispatcher](#dispatcher)
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
| `~/.config/odoo_client` | `/usr/local/share/odoo-sdk-config` | `ODOO_SDK_CONFIG` — points at the dir; the SDK probes it for `config.toml`/`config.ini`. |
| `~/.config/pr-automation` | `/usr/local/share/pr-automation` | `PR_AUTOMATION_CONFIG_DIR` — `create-pr`'s global and per-project config. |
| `~/.config/coderabbit` | `/usr/local/share/coderabbit-config` | `CODERABBIT_CONFIG_DIR` — CodeRabbit CLI config/auth state. |
| `~/.config/odoo-dev` | `/usr/local/share/odoo-dev` | `ODOO_DEV_STATE_DIR` — the [odoo-dev plugin](#odoo-dev-plugin)'s repo map and handoff artifacts. |
| `~/.config/devcontainer/shell-history` | `/usr/local/share/shell-history` | Bash history, shared across containers and projects. |

See [`devcontainer-features/src/personal-features/NOTES.md`](devcontainer-features/src/personal-features/NOTES.md) for more detail, including the Windows and WSL notes.

## `odoo_client`

A Python SDK for Odoo ERP access via XML-RPC and JSON-RPC, with a built-in [MCP](https://modelcontextprotocol.io) server so AI agents can call Odoo operations as tools.

### Subsystems

The complete map of `src/odoo_client/` — detail lives in the Sphinx docs.

| Package | Role |
| --- | --- |
| `client` | `OdooClient` — connects to an Odoo server and returns model proxies via `client["model.name"]`; reads settings from keyword args or a `.odoo_client.ini` file. |
| `records` | `OdooRecordset` — an ordered set of records; supports `search`, `read`, `write`, `create`, `unlink`, and x2many field commands. |
| `query` | `DomainExpression` — composes search domains from `Condition` nodes (`&`, `\|`, `!`) and serializes to the wire format. |
| `fields` | Field-value adaptation and x2many command normalization. |
| `commands` | `Command` / `Registry` — Odoo operations wrapped as named, dependency-injected commands; the `Registry` owns each command's shared client/state/config. |
| `mcp` | `OdooMCPServer` — FastMCP server exposing an explicit, hand-written tool set (`build_explicit_tools`) over a `Registry`, with no auto-reflection of command signatures. |
| `cli` | Command-line companion for Odoo task time-tracking. |
| `tui` | Terminal UI that explores sessions over a date window as per-lane timeline bars. |
| `transport` | XML-RPC / JSON-RPC transport and the SDK's error hierarchy. |
| `env` | Metadata cache for Odoo model schema. |
| `state` | `LocalStateClient` (SQLite event store), `LocalConfig` (File > Env > Default settings), and the dispatcher's stores: the job store (`jobs.py`), the run record (`runs_store.py`), artifacts (`artifacts.py`), migrations (`migrations.py`) and dispatch state (`dispatch_state.py`). |
| `sessionization` | Pure Transform + Load core that derives sessions from tracked events; unaware of SQLite, git, and MCP. |
| `adapters` | Bridge the pure `sessionization` core to stateful edges — the SQLite `events` table and external systems. |
| `billing` | Turns tracked work into billed Odoo timesheet rows (`account.analytic.line`). |
| `services` | Thin single-call Odoo wrappers that commands compose. |

### Quickstart

```python
from odoo_client import OdooClient

client = OdooClient(url="https://myodoo.example.com", db="mydb", username="admin", password="...")
tasks = client["project.task"].search([("stage_id.name", "=", "In Progress")], limit=20).read(["name", "user_ids"])
```

Connection settings can also be stored in `.odoo_client.ini`:

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
from odoo_client import OdooClient, OdooMCPServer, Registry
from odoo_client.commands.builtin import register_builtins
from odoo_client.mcp.tools import build_explicit_tools

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
cd libraries/odoo_client
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

The seven slash commands in [`plugins/odoo-dev/commands/`](plugins/odoo-dev/commands) are the only entry points: `/odoo-dev:plan`, `/odoo-dev:task`, `/odoo-dev:test`, `/odoo-dev:pr`, `/odoo-dev:upgrade`, `/odoo-dev:implement` and `/odoo-dev:upgrade-implement`. What follows is a map; the `SKILL.md`, agent and command files are the territory.

### Inventory

**16 skills, 6 subagents, 7 slash commands** — the counts [`plugins/odoo-dev/scripts/validate.sh`](plugins/odoo-dev/scripts/validate.sh) asserts. Reference a skill as `odoo-dev:<name>`; frontmatter names stay bare and the namespace is derived. Individual plugin skills cannot be switched off — `skillOverrides` never reaches a plugin-sourced skill — so the only granularity is the whole plugin or none of it.

| Group | Skills (under [`plugins/odoo-dev/skills/`](plugins/odoo-dev/skills)) |
| --- | --- |
| Delivery — the doer chain, one step per skill | `odoo-repo-map`, `odoo-prior-art`, `odoo-task-env`, `odoo-test-run`, `odoo-code-review`, `odoo-pr`, `odoo-release` |
| Consulting | `discovery-notes`, `estimate`, `odoo-design-doc` |
| Platform | `odoo-devcontainer`, `odoo-populate-db`, `odoo-session-db`, `odoo-upgrade`, `odoo-upgrade-project`, `principles` |

Agents are plain subagents, in [`plugins/odoo-dev/agents/`](plugins/odoo-dev/agents). Their names carry the `odoo-dev-` prefix because plugin agent names are **not** auto-namespaced and would otherwise collide across plugins.

| Agent | Owns | Writes (section of `task.json`) |
| --- | --- | --- |
| [`odoo-dev-builder`](plugins/odoo-dev/agents/odoo-dev-builder.md) | One task, one worktree: code, tests, a green self-test, conventional commits | `build` |
| [`odoo-dev-tester`](plugins/odoo-dev/agents/odoo-dev-tester.md) | Independent evidence: tests, tours, the Odoo review lens. **Cannot edit code** | `review` (`test` is written by the test script) |
| [`odoo-dev-pr`](plugins/odoo-dev/agents/odoo-dev-pr.md) | Push, local CodeRabbit review, draft PR, promotion, chatter notes | nothing |
| [`odoo-dev-upgrader`](plugins/odoo-dev/agents/odoo-dev-upgrader.md) | Cross-version porting, 16 → 17 → 18 → 19; stops at the port | `build` (and `upgrade`, only through `upgrade-state.sh`) |
| [`odoo-dev-implementer`](plugins/odoo-dev/agents/odoo-dev-implementer.md) | One task, unattended, from the host's brief to a draft PR: it chains spec, build, test, iterate and pr, never stops to ask, and records each open question with its assumption | none of its own; the chained stages write theirs |
| [`odoo-dev-upgrade-implementer`](plugins/odoo-dev/agents/odoo-dev-upgrade-implementer.md) | One task's port to a target series, unattended, from the brief to a draft PR: upgrade, test, iterate, pr | none of its own; the chained stages write theirs |

One slash command per stage, in [`plugins/odoo-dev/commands/`](plugins/odoo-dev/commands):

| Command | Does |
| --- | --- |
| `/odoo-dev:plan` | Plans one task in plan mode: prior-art verdict, acceptance criteria, a step plan with a verify check per step, hours. Approval writes the `plan` section |
| `/odoo-dev:task` | Delivers one task in its own worktree: existing-work check, branch, stack, code and tests, a green self-test, conventional commits |
| `/odoo-dev:test` | Produces independent evidence for one task branch: tests and tours on a throwaway database, plus the Odoo review lens |
| `/odoo-dev:pr` | Opens a client-visible draft PR for a finished task branch, or a release PR (`/odoo-dev:pr release <from> <to>`, no task directory needed) |
| `/odoo-dev:upgrade` | Ports one task's modules to a target major series. Stops at the port |
| `/odoo-dev:implement` | Implements one task unattended, from the host's brief to a draft PR, by chaining spec, build, test, iterate and pr |
| `/odoo-dev:upgrade-implement` | Ports one task's modules unattended, from the brief to a draft PR, by chaining upgrade, test, iterate and pr |

`task`, `test`, `upgrade` and `pr` are forked: each resolves the paths its agent needs and dispatches it. `/odoo-dev:plan` runs inline: it enters plan mode, writes the spec into the native plan file, and on approval stores it as the `plan` section. Commands are model-invocable (#1060 removed `disable-model-invocation: true` from the five original commands), so the two implementers chain the stages themselves. A watched session still types the next command once it has read what the last one returned. Iterate is a re-run of `/odoo-dev:task`: the builder reads the latest test and review sections and fixes those failures first.

### Dispatch

Routing ends in a `Task` call, not in a recommendation: naming the owning agent in prose dispatches nothing. `subagent_type` is the namespaced name (`odoo-dev:odoo-dev-builder`, and the same shape for the other five) — a bare name does not resolve, and the fallback to `general-purpose` drops every `skills:` preload, `disallowedTools` entry and hook the definition carries.

Every spawn prompt carries two absolute paths, typed out in full in every call: the **artifacts directory** and [`scripts/artifact.sh`](plugins/odoo-dev/scripts/artifact.sh). A subagent's Bash call inherits no environment and keeps no state from the call before it, so a variable name in a command is not a path — it expands to nothing and the command runs without it. There is no evidence gate in front of any of this: the artifacts are evidence an agent reads and reports on, never a precondition that blocks the work. Every command ends with one fixed status block (`status`, `artifact`, one sentence, `next`) and one small table for its stage.

| Workflow | Chain |
| --- | --- |
| Plan | `/odoo-dev:plan` → `plan` section → `/odoo-dev:task` |
| Task delivery | `builder` → `tester` → `pr`; red tests go back to `builder` |
| Version upgrade | `upgrader` → `tester` → `pr` |
| Unattended task | `/odoo-dev:implement` (`odoo-dev-implementer`) chains the stages above to a draft PR |
| Unattended upgrade | `/odoo-dev:upgrade-implement` (`odoo-dev-upgrade-implementer`) chains upgrade, test, iterate and pr |
| Release | `pr release <from> <to>` → draft release PR + a note on every included task; no task directory |

### Handoff artifacts

One file per task, `<state dir>/tasks/<id>/task.json`, written only through `artifact.sh`, with five sections: `plan` (the approved plan, verbatim), `build` (`worktree`, `branch`, `modules`, `odoo_version`, `default_branch`), `test` (one entry per module, written by `run-tests.sh --put` and never re-typed by an agent), `review` (`findings`, `criteria_results`) and `upgrade` (`inputs`, `run`, `modules` and more, written only by `upgrade-state.sh`). Each command reads and writes only its section. A section exists only when a later step branches on it and no script already provides the same thing.

`artifact.sh set` validates the payload (JSON, the required keys of the section, cheap types) and replaces the section in place with an atomic write; `get` reads a section or a key, `--raw` unquotes the plan markdown; `list` names the sections on disk; `sections` prints the schema. The file holds the latest state only. A re-run is safe through the scripts — `pr-open.sh` edits the open PR on the head and `writeback.sh --dedupe-key` posts once — not through a record.

### Hooks

Two `PreToolUse` matchers, declared in [`hooks/hooks.json`](plugins/odoo-dev/hooks/hooks.json) and shipped inside the plugin: `Bash` carries three hooks, and `Edit|Write|NotebookEdit` carries the unattended guard. Nothing is written to your `settings.json`; enabling or disabling the plugin turns them on and off with everything else, and all stay silent unless they have something to say.

| Hook | Fires on | Denies |
| --- | --- | --- |
| [`bash-allowlist.sh`](plugins/odoo-dev/hooks/bash-allowlist.sh) | Every `Bash` call whose payload reports `agent_type` `odoo-dev-tester` | Anything off a short allowlist of sanctioned scripts and read-only `git`. This is what makes "cannot edit code" a property of the harness rather than a promise in a prompt; `disallowedTools` never covered `Bash` |
| [`unattended-guard.sh`](plugins/odoo-dev/hooks/unattended-guard.sh) | `Bash` and `Edit`/`Write`/`NotebookEdit`, when `ODOO_UNATTENDED` is set or an `odoo-dev-*` agent runs | The irreversible lines of [`prompts/unattended-invariants.md`](plugins/odoo-dev/prompts/unattended-invariants.md), such as a force-push. The dispatcher sets `ODOO_UNATTENDED=1`. Without `node` it denies rather than allow an unchecked call |
| [`commit-hook.sh`](plugins/odoo-dev/hooks/commit-hook.sh) | `git commit`, from any agent | A commit carrying changes to a module whose `__manifest__.py` `version` has not moved since the base commit. Everything it cannot attribute — no module, no repo, a merge in progress, an explicit pathspec — passes in silence |

### State

Mutable state lives **outside** the plugin tree, at `${ODOO_DEV_STATE_DIR:-$HOME/.local/share/odoo-dev}`: `repo-map.json` and `tasks/<task_id>/task.json` (five sections: `plan`, `build`, `test`, `review`, `upgrade`). Inside a container `personal-features` sets `ODOO_DEV_STATE_DIR` to `/usr/local/share/odoo-dev` and bind-mounts host `~/.config/odoo-dev` there, so state outlives a rebuild — the `$HOME` default is what a bare checkout gets, and inside a container it resolves to image storage a rebuild discards. [`scripts/state-dir.sh`](plugins/odoo-dev/scripts/state-dir.sh) is the single definition of that rule; nothing else may hard-code the default. Seed the dir with `plugins/odoo-dev/scripts/bootstrap-state.sh`, which is idempotent and seeds only what is absent.

### Verify

```bash
plugins/odoo-dev/scripts/setup.sh --check   # is this machine ready? report only, non-zero if not
plugins/odoo-dev/scripts/validate.sh        # every gate in one call; offline
```

`validate.sh` covers the manifest and component inventory, frontmatter and body limits, router completeness, agent definitions, namespacing, hard-coded paths, stray skills, eval-suite structure, the offline script test suites, shell syntax, and release-version drift. [`.github/workflows/plugin-odoo-dev.yaml`](.github/workflows/plugin-odoo-dev.yaml) runs it on CI, paths-filtered to the plugin, alongside the per-skill script suites and a pinned shellcheck; it sets `REQUIRE_CLAUDE=1` so the one skippable gate (`claude plugin validate`) becomes a hard failure there, and a green CI run is never one where that gate quietly did not happen.

release-please treats `plugins/odoo-dev` as its own package (component `odoo-dev-plugin`) and bumps `version` in `plugins/odoo-dev/.claude-plugin/plugin.json`, which is the only thing `claude plugin update` reacts to — a merge that does not move that string ships nothing to anyone.

## odoo-orchestrator plugin

The host-side half of the task orchestrator, at [`plugins/odoo-orchestrator/`](plugins/odoo-orchestrator). It runs on the host and knows about tasks and one dispatcher. It knows nothing about Odoo modules, worktrees or containers. The master design is [`docs/design/devcontainer-task-orchestrator.html`](docs/design/devcontainer-task-orchestrator.html).

One command, [`/odoo-orchestrator:run`](plugins/odoo-orchestrator/commands/run.md) (`[<repo>:]<task-id> ...`), pulls the Odoo tasks, hardens each into a brief, takes approval per task, and queues the approved set on the [dispatcher](#dispatcher). A brief states behaviour and outcome. It never names a file, module, model, method, command or build step: the container decides how.

| Script | Role |
| --- | --- |
| [`scripts/dispatch.sh`](plugins/odoo-orchestrator/scripts/dispatch.sh) | The one place the orchestrator calls the dispatcher. It uses `dispatch` on `PATH`, or else `uv run` against `ODOO_ORCHESTRATOR_DISPATCHER_DIR`. Dispatcher exit codes pass through, plus 74 (cannot create the orchestrator home) and 127 (no dispatcher found). For `run`, stdout is also saved under `<home>/runs/` |
| [`scripts/orchestrator.py`](plugins/odoo-orchestrator/scripts/orchestrator.py) | Standard-library helpers: `plan-path`, `approve`, `lint`, `unhandled`, `handled`, `friction`, `records`. It keeps drafts, approvals, friction and handled dead letters |

| Variable | Meaning |
| --- | --- |
| `ODOO_ORCHESTRATOR_HOME` | The orchestrator home. Default `~/.local/state/odoo-orchestrator`. It holds `briefs/`, `approved/`, `handled.txt`, `friction-done.txt`, `runs/` and `friction/` |
| `ODOO_ORCHESTRATOR_DISPATCHER_DIR` | The `libraries/dispatcher` directory of a checkout, used when `dispatch` is not on `PATH` |

`bash plugins/odoo-orchestrator/scripts/validate.sh` runs the plugin gates.

## dispatcher

The Python package at [`libraries/dispatcher/`](libraries/dispatcher): a host-side task dispatcher that runs one Odoo task per devcontainer session and records it as a run. It depends on `odoo_client` for the job store, run record and dispatch state. The entry point is `dispatch` (`dispatcher.cli:main`).

```bash
dispatch run '<JSON array>'   # submit the approved set, then admit and run until nothing is pending
dispatch run                  # no argument: resume whatever is PENDING
dispatch dead-letters         # print the dead letter list as JSON (read only, takes no lock)
```

The JSON array is one argument. Each element is `{"repo": ..., "task": ..., "brief": <path>}` with an optional `"round"` and an optional `"entry"` (`/odoo-dev:implement` or `/odoo-dev:upgrade-implement`). `run` prints one JSON object (`batch_id`, `jobs`) on stdout. A singleton lock is taken first. To run a second dispatcher, point it at another state root with `ODOO_DISPATCH_STATE_DIR`.

| Exit code | Meaning |
| --- | --- |
| 0 | The queue drained. Dead letters are data, not failure |
| 1 | A job raised inside the dispatcher, the run stopped fatally, or a job is still `PENDING` at the end |
| 2 | Bad arguments, bad settings or an unreadable brief |
| 3 | Another dispatcher holds the lock |

Settings come from the environment ([`settings.py`](libraries/dispatcher/src/dispatcher/settings.py) has the full list). Two are required: `DISPATCH_REPOS_DIR` (or `REPOS_DIR`) and `DISPATCH_CONFIG_ROOT`. The config owner is found per repo under `DISPATCH_CONFIG_ROOT/github.com/*/<repo>`.

| Setting | Default |
| --- | --- |
| `DISPATCH_REPO_OWNER` | unset; a tie-breaker when one repo name exists under two owners |
| `DISPATCH_ENTRY` | `/odoo-dev:implement`; a job's own `entry` overrides it |
| `DISPATCH_ARTIFACTS_DIR` | `~/.config/odoo-dev/dispatch/artifacts` (must sit under `~/.config/odoo-dev`) |
| `DISPATCH_REPO_MAP_FILE` | `~/.config/odoo-dev/repo-map.json`; copied into each job's bundle so `ODOO_DEV_STATE_DIR` resolves the project |
| `DISPATCH_ADMIT_TIMEOUT_S` | `1800` |
| `DISPATCH_ADDITIONAL_FEATURES` | the `personal-features` Feature |
| `MIN_FREE_GB`, `DISPATCH_SETTLE_S`, `DISPATCH_POLL_S`, `DISPATCH_DEADLINE_S` | `6`, `5`, `15`, `21600` |
| `DISPATCH_PLUGIN_ROOT`, `DISPATCH_INVARIANTS_FILE`, `DISPATCH_CLAUDE_HOME` | the odoo-dev plugin root, its `prompts/unattended-invariants.md`, and `~/.claude` |

## Repo and Feature structure

The packages sit under `devcontainer-features/` and `libraries/`, the plugins under `plugins/`, the design export under `docs/design/`, with shared scripts in `scripts/`:

```
├── devcontainer-features
│   ├── src/personal-features/     # devcontainer Feature — install.sh, hooks/, create-pr/, claude-event-hook/, sync-claude-*, NOTES.md
│   ├── src/second-brain/          # devcontainer Feature — bind-mounts the host knowledge base, NOTES.md
│   ├── src/debian-eol-archives/   # devcontainer Feature — repoints apt at the archives on an EOL Debian base, NOTES.md
│   └── test/                      # feature test scenarios, one dir per Feature
├── libraries
│   ├── odoo_client/                  # owns src/, tests/, docs/, examples/, tools/ directly
│   │   ├── src/odoo_client/          # adapters, billing, cli, client, commands, env, fields, mcp, query, records, sessionization, services, state, tracking, transport, tui
│   │   ├── tests/                 # one test_<pkg>/ per subpackage
│   │   ├── docs/source/           # Sphinx docs
│   │   ├── examples/, tools/
│   │   └── pyproject.toml, Makefile
│   └── dispatcher/                # host-side task dispatcher — src/dispatcher/, tests/, pyproject.toml, Makefile
├── plugins
│   ├── odoo-dev/                  # Claude Code plugin — agents/, commands/, hooks/, prompts/, scripts/, skills/, evals/
│   └── odoo-orchestrator/         # Claude Code plugin — commands/run.md, scripts/ (dispatch.sh, orchestrator.py, validate.sh, tests/)
├── docs/design/                   # devcontainer-task-orchestrator.html — the master design of the orchestrator stack
└── scripts/                       # google_oauth_setup.py
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

### `odoo_client`

```bash
cd libraries/odoo_client
uv sync                      # install all dependency groups

# Unit tests
uv run python -m unittest discover -s tests -p "test_*.py" -t .

# Coverage (enforces 90 % threshold)
make coverage

# Static analysis
make quality
```

### `dispatcher`

```bash
cd libraries/dispatcher    # or: make -C libraries/dispatcher <target>
uv sync --group test
make coverage              # pytest under coverage; fails below fail_under in pyproject.toml
make static                # black --check and complexipy
make format                # black src tests
```

[`.github/workflows/dispatcher.yaml`](.github/workflows/dispatcher.yaml) runs the lock check, `make coverage` and the static-analysis gate.

### Plugin gates

```bash
bash plugins/odoo-dev/scripts/validate.sh
bash plugins/odoo-orchestrator/scripts/validate.sh
```
