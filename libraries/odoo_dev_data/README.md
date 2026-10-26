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

`python -m odoo_dev_data doctor [--json]` prints the report.

## Test

```
uv run --package odoo_dev_data pytest libraries/odoo_dev_data/tests -q
```
