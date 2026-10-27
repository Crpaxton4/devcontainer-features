# odoo_dev_data

The data layer of the odoo-dev suite. It owns the state root, the config file,
`state.db` with its migrations, and the stores on that root.

Apps never import this package. The logic layer `odoo_dev_core` imports it.

## State root

`home.py` is the only place that names the layout. It resolves the root from
`ODOO_DEV_HOME`. When the variable is unset or empty, the root is
`~/.config/odoo-dev`. Every container bind-mounts the root at
`/usr/local/share/odoo-dev` (`CONTAINER_HOME`).

```
$ODOO_DEV_HOME/
  config.toml      settings
  repo-map.json    project to repo map
  state.db         tasks, jobs, events (SQLite)
  usage.db         usage log (SQLite)
  tasks/<id>/jobs/<job>/   job bundles
  locks/           lock files
```

`ensure_home()` is the only function that creates directories. It creates the
root with mode `0o700`.

## Modules

| Module | Holds |
|---|---|
| `home.py` | the state root resolver and the layout |
| `config.py` | `LocalConfig`: `config.toml` reader, `[model_ids]` writer, `connection_values()` |
| `db/` | `connect()`, the migration runner and the numbered migrations of `state.db` |
| `events.py` | `EventStore`: events, derived sessions, upload ledger, chatter dedupe, settings |
| `doctor.py` | `doctor()` and `render()`: a read-only report of the root |
| `migrate.py` | `migrate()`: imports every legacy store into the root, idempotent, never deletes |
| `jobs.py` | `JobStore`: the job row, the only run record |
| `repo_map.py` | `repo-map.json` parser, validator, writer and project resolver |
| `tasks.py` | `task.json`, `brief.md`, `draft-N.md` and the one friction list per task |
| `locks.py` | `exclusive(name)`: flock files under `locks/` |
| `adapters/` | `git`, `github` and `odoo` resync sources that satisfy the core ports |

`odoo-dev doctor [--json]` prints the report. `odoo-dev doctor --migrate [--dry-run]`
runs the import. `python -m odoo_dev_data` runs the same doctor without the CLI.

It depends on nothing in the workspace (`tomli` on Python 3.10 only).

## Test

```
uv run --package odoo_dev_data pytest libraries/odoo_dev_data/tests -q
```
