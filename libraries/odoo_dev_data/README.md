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

## Planned modules

`config.py`, `db/`, `events.py` and `doctor.py` arrive in later tasks.

## Test

```
uv run --package odoo_dev_data pytest libraries/odoo_dev_data/tests -q
```
