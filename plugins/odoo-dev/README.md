# odoo-dev plugin

Odoo consulting and delivery as one Claude Code plugin. It runs in the
devcontainer, next to the `odoo-dev` CLI and the `odoo-mcp` server of the suite.
The repo marketplace `.claude-plugin/marketplace.json` serves it.

```
claude plugin marketplace add Crpaxton4/devcontainer-features
claude plugin install odoo-dev@devcontainer-features
```

## Requires

`requires.json` states the version of the `odoo-dev` CLI that this plugin needs
(`{"odoo-dev": ">=9.0"}`). `scripts/writeback.sh` and `scripts/setup.sh` read it.
They stop with an upgrade message when the CLI is missing or older. Upgrade with
`uv sync --all-packages`.

## Skills (`skills/<name>/SKILL.md`, used as `odoo-dev:<name>`)

| Group | Skills |
|---|---|
| Delivery | `odoo-repo-map`, `odoo-prior-art`, `odoo-task-env`, `odoo-test-run`, `odoo-code-review`, `odoo-pr`, `odoo-release` |
| Consulting | `discovery-notes`, `estimate`, `odoo-design-doc` |
| Platform | `odoo-devcontainer`, `odoo-populate-db`, `odoo-session-db`, `odoo-upgrade`, `odoo-upgrade-project`, `principles` |

## Commands (`commands/`) and agents (`agents/`)

Plugin agent names carry the `odoo-dev-` prefix, because plugin agent names are not namespaced.

| Command | Agent | Does |
|---|---|---|
| `/odoo-dev:plan` | none (plan mode) | Plan one task and write the `plan` section. |
| `/odoo-dev:task` | `odoo-dev-builder` | Deliver one task in its own worktree. |
| `/odoo-dev:test` | `odoo-dev-tester` | Produce test evidence. It cannot edit code. |
| `/odoo-dev:pr` | `odoo-dev-pr` | Open the draft PR, or a release PR. |
| `/odoo-dev:upgrade` | `odoo-dev-upgrader` | Port modules to a target series. |
| `/odoo-dev:implement` | `odoo-dev-implementer` | Run one task unattended, from brief to draft PR. |
| `/odoo-dev:upgrade-implement` | `odoo-dev-upgrade-implementer` | Run one port unattended, from brief to draft PR. |

The dispatcher starts a job with `/odoo-dev:implement` or `/odoo-dev:upgrade-implement`.

## Hooks (`hooks/hooks.json`, `PreToolUse`)

| Hook | Does |
|---|---|
| `hooks/bash-allowlist.sh` | For `Bash` calls of `odoo-dev-tester`: denies all but a short allowlist. |
| `hooks/unattended-guard.sh` | For `Bash` and `Edit\|Write\|NotebookEdit` when `ODOO_UNATTENDED` is set or an `odoo-dev-*` agent runs: denies the irreversible lines of `prompts/unattended-invariants.md`. |

## Scripts (`scripts/`)

| Script | Does |
|---|---|
| `state-dir.sh` | Resolves `tasks/<id>` under `ODOO_DEV_HOME`. |
| `artifact.sh` | The only reader and writer of `tasks/<id>/task.json`. |
| `writeback.sh` | Posts notes and activities to Odoo through `odoo-dev cmd`. |
| `module-classify.sh` | Sorts the modules of a diff into install, update and remove. |
| `json-get.sh` | The one JSON reader of the shell scripts. |
| `setup.sh` | Checks (`--check`) or installs the dependencies of the plugin. |
| `check-stray-skills.sh`, `check-suite-registry.sh`, `check-tool-contract.sh` | Gates that `validate.sh` runs. |

## Verify

```
plugins/odoo-dev/scripts/setup.sh --check   # is this machine ready? report only
plugins/odoo-dev/scripts/validate.sh        # every gate, offline
```

`evals/` holds the trigger evals of the skills. CI runs `validate.sh` in
`.github/workflows/plugin-odoo-dev.yaml`.
