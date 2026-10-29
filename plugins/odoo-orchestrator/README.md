# odoo-orchestrator plugin

The host-side half of the task orchestrator. It runs on the host, in a Claude
Code session. It knows tasks and one dispatcher. It knows nothing about Odoo
modules, worktrees or containers. The dispatcher and the web UI run in the
compose stack (see `stack/README.md`). The master design is
`docs/design/devcontainer-task-orchestrator.html`.

## Command

`/odoo-orchestrator:run [<repo>:]<task-id> ...` (`commands/run.md`) pulls the Odoo
tasks, hardens each into a brief, takes approval per task, and queues the approved
set on the dispatcher. A brief states behaviour and outcome. It never names a
file, module, model, method or build step: the container decides how.

## Skills

`/odoo-orchestrator:report-incident [description]` (`skills/report-incident/SKILL.md`)
drafts a GitHub issue for an incident, with a current environment block and no
private data, and files it after you approve.

## Scripts (`scripts/`)

| Script | Does |
|---|---|
| `dispatch.sh` | The one place the orchestrator calls the dispatcher. It is a front end for `docker compose` with `stack/compose.yaml`. `add '<json>'` submits the jobs and starts the dispatcher and web UI containers. Any other verb runs `dispatch` with those arguments. |
| `orchestrator.sh` | Runs `orchestrator.py` in the `orchestrator` service of the stack. |
| `orchestrator.py` | Helpers: `plan`, `approve`, `approved`, `approve-job`, `lint`, `unhandled`, `handled`, `friction`, `records`, `batches`, `batch-done`. |
| `validate.sh` | The gates of this plugin. |

Briefs, approvals and the handled and reported marks live in the state root
`ODOO_DEV_HOME` (`tasks/<task>/brief.md`, `state.db`).

## Environment

| Variable | Meaning |
|---|---|
| `ODOO_ORCHESTRATOR_CHECKOUT` | Host path of the checkout that holds `stack/compose.yaml`. Required. |
| `ODOO_DEV_HOME` | The state root. Default `~/.config/odoo-dev`. |

The scripts exit 127 when the stack file is not found. The exit codes of the
dispatcher pass through.

## Verify

```
bash plugins/odoo-orchestrator/scripts/validate.sh
```

CI runs it in `.github/workflows/plugin-orchestrator.yaml`.
