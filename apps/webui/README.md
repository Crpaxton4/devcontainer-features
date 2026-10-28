# webui

A read-only local web interface for the dispatcher (UI layer). It is one Flask
app (`webui.create_app`). `python -m webui` serves it on `0.0.0.0:8765`. The
compose stack publishes that port on `127.0.0.1` only. The pages have no write
route and open SQLite with `mode=ro`.

## Pages

| Route | Shows |
|---|---|
| `/jobs`, `/jobs/<job_id>` | Every job by state, refreshed every 3 s. A detail page has the run outcome, the token and cost figures and the files of the job bundle (`/jobs/<job_id>/files/<name>`). |
| `/queue` | The `PENDING` and `RUNNING` jobs. |
| `/friction` | The friction entries of each task. |
| `/usage` | The usage lines of the last days, grouped by kind and name. |

A page group is one module with a `Blueprint`. Add one line to `PAGES` in
`webui/__init__.py` to register it. `PAGES` also builds the navigation.

## Dependencies

`dispatcher` (it reads through `dispatcher.reporting`) and `flask`. The state root
is `ODOO_DEV_HOME`. The `webui` service of `stack/compose.yaml` mounts it read-write,
because a `:ro` bind breaks a WAL reader.

## Test

```
uv run --package webui pytest apps/webui/tests
```
