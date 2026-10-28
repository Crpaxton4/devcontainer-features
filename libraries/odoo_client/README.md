# odoo_client

The Odoo SDK of the odoo-dev suite (DATA layer). It talks to an Odoo server
over XML-RPC or JSON-2 and returns model proxies. It has no dependencies. It
does not read config files: an app bootstrap builds the settings and passes them in.

## Public API

| Name | Role |
|---|---|
| `OdooClient` | Connects to a server. `client["model.name"]` returns an `OdooRecordset`. |
| `OdooConnectionSettings` | The value object that the transports consume (`settings.py`). |
| `OdooRecordset`, `Record` | `search`, `read`, `write`, `create`, `unlink` and x2many commands (`records/`). |
| `Domain`, `DomainExpression` | Compose search domains and serialize them to the wire format (`query/`). |
| `OdooRpcExecutor`, `OdooJson2Executor` | The two transports (`transport/`). |
| `OdooError` and subclasses | The error hierarchy (`transport/errors.py`). |

The packages `fields/` and `env/` hold field adaptation and the model schema cache.

```python
from odoo_client import OdooClient, OdooConnectionSettings

client = OdooClient(settings=OdooConnectionSettings(url="https://odoo.example.com", db="mydb", ...))
tasks = client["project.task"].search([("stage_id.name", "=", "In Progress")], limit=20).read(["name"])
```

See `OdooConnectionSettings` for the full list of fields.

## Dependencies

None. Apps import it only inside their `bootstrap` module (import-linter contract
"apps never import a data package").

## Test

```
uv run --package odoo_client pytest libraries/odoo_client/tests
make -C libraries/odoo_client coverage    # tests under coverage
make -C libraries/odoo_client docs        # Sphinx docs from docs/source
```
