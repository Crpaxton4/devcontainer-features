# odoo-dev suite

The odoo-dev suite is a uv workspace of nine Python packages that track and bill
Odoo work. `odoo_client` is the pure Odoo RPC client (XML-RPC or JSON-2) with a
recordset abstraction. `odoo_dev_data` owns the state root `ODOO_DEV_HOME`, with
the config file, the repo map, `state.db` and the task folders. `odoo_dev_core`
owns the command registry (35 builtin commands), the workflows, the sessionization
and the billing. `dispatcher` runs queued jobs in containers. `usage_log` records
feature use. Four apps sit on top: `odoo-dev` (`odoo_cli`), `odoo-mcp`
(`odoo_mcp`), `odoo-tui` (`odoo_tui`) and `webui`.

## Install

```bash
uv sync --all-packages --group dev
```

## Configure a connection

The state root is `$ODOO_DEV_HOME`. The default is `~/.config/odoo-dev`. Each
connection setting resolves in this order: the file, then the environment, then
the default. The file is `$ODOO_DEV_HOME/config.toml`, section `[connection]`:

```toml
[connection]
url = "https://example.odoo.com"
db = "example-db"
username = "user@example.com"
password = "your-password-or-api-key"
```

The environment variables are `ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`,
`ODOO_PASSWORD`, `ODOO_API_KEY` and `ODOO_TRANSPORT` (`xmlrpc` or `json2`).
`ODOO_TIMEOUT` sets the request timeout. There is no INI file.

Run `odoo-dev doctor` to check the setup. It prints every path of the state root,
the schema version of `state.db` and a result for each check. `odoo-dev doctor
--migrate` moves the stores of an older version into the state root. It is
idempotent and it never deletes a legacy store.

## Model ids

Some Odoo fields hold an `ir.model` *id* and not a model name. Examples are
`mail.activity.res_model_id`, `ir.actions.*.model_id` and a domain that filters
on a model reference. The suite never reads `ir.model` to resolve one. A
least-privileged service account must not hold that administrative table. The ids
come from a hand-managed `[model_ids]` section:

```toml
[model_ids]
"project.task" = 123
"res.partner" = 77
```

Or from `ODOO_MODEL_IDS`, as comma-separated or whitespace-separated `model:id`
pairs:

```bash
export ODOO_MODEL_IDS="project.task:123,res.partner:77"
```

The precedence matches the rest of the config: file, then environment, then
default. It applies for each model name, so the two sources are merged and the
file wins for a model that both name. The default is an empty map. A value that is
not a positive integer is rejected when the config loads.

An operator who holds the privilege fills the section once with the `get_models`
command. It writes the ids that it resolved into `config.toml`, so nobody types
an entry by hand:

```bash
odoo-dev cmd get_models --args '{"persist": ["project.task"]}'
```

`persist` names the models to record. Everything else in the file is left as it
was, and `ODOO_MODEL_IDS` is never written. If the file cannot be written, the read
still succeeds and the result carries a `warning`. If code asks for a model with
no entry, `LocalConfig.require_model_id` raises a `ValueError` that names the entry
to add. `schedule_activity` is the first user: `mail.activity.res_model_id` is a
required `ir.model` many2one, so scheduling an activity on a `project.task` needs
`"project.task"` in `[model_ids]`.

## Client quickstart

`odoo_client` reads no file and no environment variable. Build the settings and
hand them to the client. The `bootstrap` module of each app does this from
`config.toml`.

```python
from odoo_client import DomainExpression, OdooClient
from odoo_client.settings import build_connection_settings

settings = build_connection_settings(
    {
        "url": "https://example.odoo.com",
        "db": "example-db",
        "username": "user@example.com",
        "password": "your-password",
    }
)
client = OdooClient.from_settings(settings)

# Or build a client for one transport directly:
# client = OdooClient.from_xml_rpc(url, db, username, password)
# client = OdooClient.from_json2(url, db, api_key)

tasks = client["project.task"]
domain = DomainExpression.normalize([("stage_id.name", "=", "In Progress")])
for task in tasks.search(domain):
    print(task.name)
```

`OdooRecordset` is the core abstraction. Every operation flows through it. Start
the API reference at `odoo_client.records.recordset`, `odoo_client.client.client`
and `odoo_client.query.domain`.

```{toctree}
:maxdepth: 2
:caption: Contents

quickstart_mcp
quickstart_tui
design/index
```

```{toctree}
:maxdepth: 1
:caption: API reference

api/odoo_client/modules
api/odoo_dev_data/modules
api/odoo_dev_core/modules
api/dispatcher/modules
api/usage_log/modules
api/odoo_cli/modules
api/odoo_mcp/modules
api/odoo_tui/modules
api/webui/modules
```
