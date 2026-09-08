# Odoo SDK

A Python SDK for Odoo's external API (XML-RPC or JSON-RPC2), built around an
Odoo-ORM-like recordset abstraction rather than raw model names and row dicts.

## Install

```bash
uv sync
```

## Configure a connection

Settings resolve constructor arguments first, then environment (`ODOO_URL`,
`ODOO_DB`, `ODOO_USERNAME`, `ODOO_PASSWORD`, `ODOO_API_KEY`, `ODOO_TRANSPORT`),
then an INI file (`.odoo_sdk.ini` in the project root, or
`~/.config/odoo_sdk/config.ini`):

```ini
[odoo]
url = https://example.odoo.com
db = example-db
username = user@example.com
password = your-password-or-api-key
```

## Model ids

Some Odoo fields hold an `ir.model` *id* rather than a model name —
`mail.activity.res_model_id`, `ir.actions.*.model_id`, and any domain that
filters on a model reference. The SDK never reads `ir.model` to resolve one:
that administrative table must not be granted to a least-privileged service
account (issues #444, #686). The ids come from a hand-managed `[model_ids]`
section instead:

```toml
[model_ids]
"project.task" = 123
"res.partner" = 77
```

```ini
[model_ids]
project.task = 123
res.partner = 77
```

Or from `ODOO_MODEL_IDS`, as comma- and/or whitespace-separated `model:id` pairs:

```bash
export ODOO_MODEL_IDS="project.task:123,res.partner:77"
```

Precedence matches the rest of the config — **File > Environment Variable >
Default** — applied per model name, so the two sources union and the file wins
for a model named by both. The default is an empty map. A value that is not a
positive integer is rejected when the config loads, rather than surfacing later
as an opaque XML-RPC fault.

An operator who *does* hold the privilege populates the section once with the
gated `get_models` tool. Ask for an unmapped model and the SDK raises a
`ValueError` naming the exact entry to add:

```python
config.model_id("mail.activity")      # -> None
config.require_model_id("mail.activity")  # -> ValueError: No ir.model id is configured for 'mail.activity'. Add it to the [model_ids] section ...
```

Unquoted TOML keys (`project.task = 123`) and explicit sub-tables
(`[model_ids.project]`) are flattened back to dotted names, so every spelling
resolves to the same entry. INI option names are lower-cased by `configparser`;
Odoo model names are lowercase anyway.

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

`OdooRecordset` is the core abstraction every operation flows through. Start the
{doc}`API reference <api/modules>` at `odoo_sdk.records.recordset`,
`odoo_sdk.client.client`, and `odoo_sdk.query.domain`.

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
