# odoo_cli

The command-line app of the odoo-dev suite (UI layer). It installs the
`odoo-dev` console script (`odoo_cli.__main__:main`, also `python -m odoo_cli`).
Hooks, plugin scripts and the dispatcher call it. `bootstrap.py` is the
composition root: it builds the `Services` bundle from the data layer and returns
the `CommandService` over the builtin commands.

## Subcommands

| Subcommand | Does |
|---|---|
| `normalize [--apply]` | Merge duplicate timesheets (dry run by default). |
| `log-event --source S` | Record a Claude Code hook event in local state. |
| `get-employee-id` | Print the `hr.employee` id of the current user. |
| `resync [--sources ...]` | Reconcile local events against git, GitHub and Odoo chatter. |
| `upload [--start --end --dry-run]` | Bill derived sessions to Odoo. |
| `cmd <name> [--args JSON]` | Run any registry command. Stdout is one JSON document. `cmd --list [--json]` lists them. |
| `doctor [--migrate] [--dry-run] [--json]` | Report on the `ODOO_DEV_HOME` state root. `--migrate` imports the legacy stores. |
| `repo-map list\|get\|resolve\|set\|remove\|validate\|defaults` | Read and edit `repo-map.json`. |

`odoo-dev --version` prints the version. `doctor` and `repo-map` need no Odoo
configuration. The Odoo client is built on first use.

## Dependencies

`odoo_dev_core`. The modules `bootstrap.py`, `doctor.py` and `repo_map.py` are the
only ones that the import-linter contract lets import the data layer.

## Test

```
uv run --package odoo_cli pytest apps/odoo_cli/tests
```
