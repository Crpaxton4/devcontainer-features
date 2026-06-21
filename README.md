# Personal Dev Container Features

This repo holds two packages: a devcontainer [Features](https://containers.dev/implementors/features/) collection (`personal-features`) and a Python SDK for Odoo ERP access (`odoo_sdk`).

## `personal-features`

Personal dev container tooling, meant to be added as a default Feature across projects. Currently it installs [Claude Code](https://code.claude.com/docs) and wraps the `claude` command so a default session automatically connects to the IDE (`--ide`), since this Feature is meant purely for use inside a VS Code dev container. It also persists Claude Code's and the GitHub CLI's auth/config in named Docker volumes shared across every project on the machine, so logging in once is enough.

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

### What persists, and where

| Volume | Mounted at | Used for |
| --- | --- | --- |
| `personal-features-claude-config` | `/usr/local/share/claude-code-home` | `~/.claude` and `~/.claude.json` are symlinked here — Claude Code's auth (`~/.claude/.credentials.json`) and settings survive rebuilds and follow you across projects. |
| `personal-features-gh-config` | `/usr/local/share/gh-cli-config` | Set as `GH_CONFIG_DIR` for the gh CLI. |

See [`devcontainer-features/src/personal-features/NOTES.md`](devcontainer-features/src/personal-features/NOTES.md) for more detail.

## `odoo_sdk`

A Python SDK for Odoo ERP access via XML-RPC and JSON-RPC, with a built-in [MCP](https://modelcontextprotocol.io) server so AI agents can call Odoo operations as tools.

### Core types

| Type | Role |
| --- | --- |
| `OdooClient` | Entry point — connects to an Odoo server and returns model proxies via `client["model.name"]`. Reads connection settings from keyword args or a `.odoo_sdk.ini` config file. |
| `OdooRecordset` | Represents an ordered set of records on a model. Supports `search`, `read`, `write`, `create`, `unlink`, and x2many field commands. |
| `DomainExpression` | Builder for Odoo search domains — composes `Condition` nodes with `&`, `\|`, `!` operators and serializes to the wire format. |
| `Command` / `Registry` | Protocol + registry for wrapping Odoo operations as named, typed commands. Each command's `execute` signature drives both programmatic use and MCP tool generation. |
| `OdooMCPServer` | FastMCP server that introspects a `Registry` at startup and exposes every registered command as an MCP tool with a fully typed input schema. |

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

Register commands in a `Registry` and serve them as MCP tools:

```python
from odoo_sdk import OdooClient, Registry
from odoo_sdk.mcp.server import OdooMCPServer

registry = Registry(OdooClient())
registry.register("get_tasks", GetTasksCommand)

server = OdooMCPServer(registry)
server.mcp.run()
```

Or run the packaged entry point directly:

```bash
odoo-mcp
```

### Setup

```bash
uv sync           # install deps + dev groups
uv run python -m unittest discover -s test/odoo_sdk -t .   # run tests
make coverage     # run tests + enforce 90 % coverage threshold
```

## Repo and Feature structure

Each package lives under `src/` with its tests under `test/`:

```
├── devcontainer-features
│   ├── src
│   │   └── personal-features      # devcontainer Feature
│   │       ├── devcontainer-feature.json
│   │       ├── install.sh
│   │       └── NOTES.md
│   └── test
│       └── personal-features      # devcontainer feature tests
│           ├── scenarios.json
│           └── test.sh
├── src
│   └── odoo_sdk                   # Python SDK
│       ├── client/
│       ├── commands/
│       ├── mcp/
│       ├── query/
│       ├── records/
│       └── transport/
├── tests
│   └── odoo_sdk                   # Python unit tests
│       ├── test_client/
│       ├── test_mcp/
│       ├── test_query/
│       └── test_records/
├── docs/                          # Sphinx docs for odoo_sdk
├── examples/                      # odoo_sdk usage examples
├── tools/                         # coverage + static-analysis scripts
├── pyproject.toml                 # odoo_sdk build + tooling config
└── Makefile                       # dev task shortcuts
```

`devcontainer-features/src/personal-features/README.md` is auto-generated by the release workflow from `devcontainer-feature.json` merged with `NOTES.md` — don't hand-edit it.

## Versioning & releases

Commit messages and PR titles follow [Conventional Commits](https://www.conventionalcommits.org/), enforced two ways:

- A local Husky `commit-msg` hook (via commitlint) checks every commit.
- A CI check, **Lint PR Title** (`.github/workflows/pr-title-lint.yaml`), checks the PR title and is a required status check on `main` — this repo only allows squash-merge, so the PR title (not the individual commit messages) is what actually lands on `main`.

[release-please](https://github.com/googleapis/release-please) watches `main` for conventional commits touching `src/personal-features` and opens/updates a release PR that bumps `version` in `src/personal-features/devcontainer-feature.json` and updates its `CHANGELOG.md`. Merging that PR cuts a GitHub Release, which automatically triggers the GHCR publish workflow (`.github/workflows/release.yaml`) — no manual `workflow_dispatch` needed, though that trigger is still available if you need to re-publish by hand.

### Publishing

Features are published to GHCR by `.github/workflows/release.yaml`, namespaced as `ghcr.io/<owner>/<repo>/<feature-id>:<version>`. *Allow GitHub Actions to create and approve pull requests* needs to be enabled in `Settings > Actions > General > Workflow permissions` for the auto-generated README PR and for release-please's release PRs.

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
uv sync                      # install all dependency groups

# Unit tests
uv run python -m unittest discover -s tests/odoo_sdk -p "test_*.py" -t .

# Coverage (enforces 90 % threshold)
make coverage

# Static analysis
make quality
```
