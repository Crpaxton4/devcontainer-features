# Odoo SDK

A Python SDK for talking to Odoo over its external API (XML-RPC or
JSON-RPC2), built around an Odoo-ORM-like recordset abstraction rather than
raw model names and row dictionaries.

## Install

The project uses [uv](https://docs.astral.sh/uv/) for environment and
dependency management:

```bash
uv sync
```

## Configure a connection

Connection settings resolve with the following precedence: explicit
constructor arguments, then environment variables
(`ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`, `ODOO_PASSWORD`, `ODOO_API_KEY`,
`ODOO_TRANSPORT`), then an INI file (`.odoo_sdk.ini` in the project root, or
`~/.config/odoo_sdk/config.ini`):

```ini
[odoo]
url = https://example.odoo.com
db = example-db
username = user@example.com
password = your-password-or-api-key
```

## Quickstart

```python
from odoo_sdk import OdooClient, DomainExpression

# Reads connection settings from env vars or .odoo_sdk.ini.
client = OdooClient()

# Or build a client explicitly for one transport:
# client = OdooClient.from_xml_rpc(url, db, username, password)
# client = OdooClient.from_json2(url, db, api_key)

tasks = client["project.task"]
domain = DomainExpression.normalize([("stage_id.name", "=", "In Progress")])
open_tasks = tasks.search(domain)

for task in open_tasks:
    print(task.name)
```

`OdooRecordset` is the core abstraction every SDK operation flows through —
see {doc}`the API reference <api/modules>` for the full surface, starting
with `odoo_sdk.records.recordset`, `odoo_sdk.client.client`, and
`odoo_sdk.query.domain`.

```{toctree}
:maxdepth: 2
:caption: Contents

quickstart_mcp
quickstart_tui
resync_google
walkthrough
api/modules
design/index
```
