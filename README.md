# Personal Dev Container Features

This repo holds the odoo-dev suite and the devcontainer
[Features](https://containers.dev/implementors/features/) that ship it. The suite
is a uv workspace of nine Python members (libraries and apps) and two Claude Code
plugins (`odoo-dev`, `odoo-orchestrator`). The Features are `personal-features`,
`second-brain` and `debian-eol-archives`. The design rules are in
[ADR-006](docs/design/adr/ADR-006-workspace-three-layers-state-root.md). The
design of the orchestrator is in
[`docs/design/devcontainer-task-orchestrator.html`](docs/design/devcontainer-task-orchestrator.html).

## Repo map

Three layers: DATA holds state and clients, LOGIC holds the commands and the
dispatcher, UI holds the apps. Import-linter enforces the direction (UI to LOGIC
to DATA). Each member has its own README.

| Path | Layer | What it is | Runs in |
|---|---|---|---|
| [`libraries/odoo_client`](libraries/odoo_client/README.md) | DATA | Odoo SDK: XML-RPC and JSON-2 transport, recordsets, domains | devcontainer, stack |
| [`libraries/odoo_dev_data`](libraries/odoo_dev_data/README.md) | DATA | State root, `config.toml`, `state.db`, stores, `doctor` | devcontainer, stack |
| [`libraries/usage_log`](libraries/usage_log/README.md) | DATA | Stdlib usage log (`usage.db`) | devcontainer, stack |
| [`libraries/odoo_dev_core`](libraries/odoo_dev_core/README.md) | LOGIC | Command registry, builtins, ports, billing, sessionization | devcontainer, stack |
| [`libraries/dispatcher`](libraries/dispatcher/README.md) | LOGIC | `dispatch`: one Odoo task per devcontainer session | compose stack |
| [`apps/odoo_cli`](apps/odoo_cli/README.md) | UI | `odoo-dev` command line | devcontainer, host |
| [`apps/odoo_mcp`](apps/odoo_mcp/README.md) | UI | `odoo-mcp` MCP server | devcontainer |
| [`apps/odoo_tui`](apps/odoo_tui/README.md) | UI | `odoo-tui` terminal UI | devcontainer |
| [`apps/webui`](apps/webui/README.md) | UI | Read-only job and queue web interface | compose stack |
| [`plugins/odoo-dev`](plugins/odoo-dev/README.md) | packaging | Claude Code plugin: skills, agents, commands, hooks | devcontainer |
| [`plugins/odoo-orchestrator`](plugins/odoo-orchestrator/README.md) | packaging | Claude Code plugin: `/odoo-orchestrator:run` | host |
| [`stack`](stack/README.md) | packaging | Compose project: dispatcher, webui, orchestrator helper | host (Docker) |
| `devcontainer-features/src/personal-features` | packaging | Feature: Claude Code, persisted config, hooks, the bundled wheels | devcontainer |
| `devcontainer-features/src/second-brain` | packaging | Feature: bind-mounts the host knowledge base | devcontainer |
| `devcontainer-features/src/debian-eol-archives` | packaging | Feature: points apt at the archives on an EOL Debian base | devcontainer |
| `docs/design`, `.github/workflows`, `scripts` | packaging | Design export, CI, shared scripts | repo |

## State

One state root holds everything the suite writes: `ODOO_DEV_HOME`. On the host it
is `~/.config/odoo-dev`. In a container it is `/usr/local/share/odoo-dev`, a bind
mount of the host directory.

```
$ODOO_DEV_HOME/
  config.toml      settings and Odoo connection (mode 0700 directory)
  repo-map.json    project to repo map
  state.db         tasks, jobs, events (SQLite)
  usage.db         usage log (SQLite)
  tasks/<id>/      task.json, brief.md, jobs/<job>/ bundles
  locks/           lock files
```

`odoo-dev doctor` reports on the root. `odoo-dev doctor --migrate [--dry-run]`
imports the legacy stores into it. `libraries/odoo_dev_data/README.md` has the detail.

## `personal-features`

A default Feature for dev containers. It installs [Claude Code](https://code.claude.com/docs),
installs the seven bundled wheels into one venv (`odoo-dev`, `odoo-mcp`, `odoo-tui`,
`usage-log`), and bind-mounts config from the host home directory. It pairs with
the official Node.js and GitHub CLI Features.

```jsonc
{
    "image": "mcr.microsoft.com/devcontainers/base:ubuntu",
    "features": {
        "ghcr.io/devcontainers/features/node:1": { "version": "lts" },
        "ghcr.io/devcontainers/features/github-cli:1": {},
        "ghcr.io/<owner>/<repo>/personal-features:1": {}
    }
}
```

The Feature wraps `claude` so a default session runs `claude --ide`. Subcommands
such as `claude mcp` pass through untouched.

### What persists, and where

`devcontainer-features/src/personal-features/persisted-paths.tsv` is the one source
of truth. `install.sh`, `setup.sh` and `setup.ps1` read or mirror it. CI checks it
against `devcontainer-feature.json`.

| Host path | Mounted at | Variable |
|---|---|---|
| `~/.claude` | `/usr/local/share/claude-home` | `CLAUDE_CONFIG_DIR` |
| `~/.config/gh` | `/usr/local/share/gh-cli-config` | `GH_CONFIG_DIR` |
| `~/.config/pr-automation` | `/usr/local/share/pr-automation` | `PR_AUTOMATION_CONFIG_DIR` |
| `~/.config/coderabbit` | `/usr/local/share/coderabbit-config` | `CODERABBIT_CONFIG_DIR` |
| `~/.coderabbit` | `/home/vscode/.coderabbit` | none |
| `~/.config/devcontainer/shell-history` | `/usr/local/share/shell-history` | none |
| `~/.mempalace` | `/usr/local/share/mempalace` | `MEMPALACE_PALACE_PATH` |
| `~/.config/odoo-dev` | `/usr/local/share/odoo-dev` | `ODOO_DEV_HOME` |

### One-time host setup

A bind mount whose source does not exist fails the container create. Create the
sources once per machine, and before `devcontainer features test`:

```sh
./setup.sh      # Linux, WSL, macOS
```

```powershell
.\setup.ps1     # native Windows host
```

`devcontainer-features/src/personal-features/NOTES.md` has the detail, including
Windows and WSL notes.

## odoo-dev plugin

Odoo consulting and delivery. It lives in [`plugins/odoo-dev/`](plugins/odoo-dev/README.md)
and the root `.claude-plugin/marketplace.json` serves it.

- 16 skills (`plugins/odoo-dev/skills/`): delivery, consulting and platform.
- 6 agents (`plugins/odoo-dev/agents/`): builder, tester, pr, upgrader and the two unattended implementers.
- 7 commands (`plugins/odoo-dev/commands/`): `plan`, `task`, `test`, `pr`, `upgrade`, `implement`, `upgrade-implement`.
- 2 `PreToolUse` hooks (`plugins/odoo-dev/hooks/`): `bash-allowlist.sh` and `unattended-guard.sh`.
- `plugins/odoo-dev/requires.json` states the `odoo-dev` CLI version that the plugin needs.

```
claude plugin marketplace add Crpaxton4/devcontainer-features
claude plugin install odoo-dev@devcontainer-features
bash plugins/odoo-dev/scripts/validate.sh     # every gate, offline
```

## odoo-orchestrator plugin

The host-side half of the task orchestrator. `/odoo-orchestrator:run` pulls tasks,
writes a brief for each, takes approval, and queues the set on the dispatcher. See
[`plugins/odoo-orchestrator/README.md`](plugins/odoo-orchestrator/README.md).

## stack

The compose project that runs the dispatcher and the web UI in containers. The host
needs only Docker. See [`stack/README.md`](stack/README.md).

## Versioning & releases

Commit messages and PR titles follow [Conventional Commits](https://www.conventionalcommits.org/).
The check **Lint PR Title** (`.github/workflows/pr-title-lint.yaml`) is required on
`main`. The repo allows squash-merge only, so the PR title is what lands on `main`.

[release-please](https://github.com/googleapis/release-please) reads
`release-please-config.json`. The nine Python members form the linked group
`odoo-dev-suite`, and all have version 9.0.0. The release PR moves them together.
`personal-features` is its own package. At release, `.github/workflows/release.yaml`
builds seven wheels (`odoo_client`, `odoo_dev_data`, `odoo_dev_core`, `odoo_cli`,
`odoo_mcp`, `odoo_tui`, `usage_log`), copies them into the Feature, and publishes the
Features to GHCR as `ghcr.io/<owner>/<repo>/<feature-id>:<version>`. GHCR packages
are private by default. Make a package public to use it without per-repo tokens.

## Testing

Python members (needs `uv`):

```bash
uv sync --all-packages --group dev
uv run pytest libraries apps
uv run lint-imports
uv run black --check libraries apps
uv run --package <name> pytest <dir>/tests     # one member
```

Plugins:

```bash
bash plugins/odoo-dev/scripts/validate.sh
bash plugins/odoo-orchestrator/scripts/validate.sh
```

Features use `devcontainer features test` from `@devcontainers/cli`
(`npm install -g @devcontainers/cli`). Run `./setup.sh` first.

```bash
devcontainer features test -p ./devcontainer-features --skip-scenarios -f personal-features -i mcr.microsoft.com/devcontainers/javascript-node:latest .
devcontainer features test -p ./devcontainer-features -f personal-features --skip-autogenerated --skip-duplicated .
```

`.github/workflows/suite.yaml` runs the member tests, `black`, `complexipy` and
`lint-imports` in CI.
