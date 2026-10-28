# usage_log

Standalone usage logging for the features this repo owns (skills, agents, slash
commands, hooks, odoo-mcp tools, odoo-dev commands). DATA layer. Python stdlib
only, Python 3.10 or later. It imports no other workspace package. The dispatcher,
the apps and the hooks call it, never the reverse.

## Switch and paths

| Item | Value |
| --- | --- |
| Root | `$ODOO_DEV_HOME` when set and not empty, else `~/.config/odoo-dev`. |
| Switch | `ODOO_DEV_USAGE_LOG=0` turns logging off. Any other value leaves it on. |
| Job id | `ODOO_JOB_ID`, when set, is added to every usage line as `job_id`. |

`usage_log` never creates the root directory. When the root does not exist, or
the switch is `0`, `record`, `declare` and the CLI do nothing and the CLI exits 0.

## Line format (the contract)

`$ODOO_DEV_HOME/usage.db`, one row per use. `$ODOO_DEV_HOME/declarations.db`, one
row per defined feature:

```sql
usage(ts TEXT, kind TEXT, name TEXT, context TEXT)   -- index (kind, name, ts)
declarations(kind TEXT, name TEXT, declared_at TEXT, context TEXT,
             PRIMARY KEY (kind, name))
```

- `ts` and `declared_at`: UTC ISO-8601 text.
- `kind`: lower case, for example `skill`, `agent`, `command`, `hook`, `mcp_tool`,
  `sdk_command`, `job`.
- `name`: the feature name without a leading `/`, for example `odoo-dev:plan`.
  `usage_log.normalise` is the one place that normalises `(kind, name)`.
- `context`: a JSON object as text, any keys. Usual keys: `source`, `session_id`,
  `agent_type`, `job_id`, `ok`, `ms`. Names and context only, never tool arguments.
- Everything is kept. There is no pruning.

## Use

```python
import usage_log
usage_log.record("skill", "odoo-dev:plan", source="odoo-dev")
usage_log.declare([{"kind": "skill", "name": "odoo-dev:plan"}])
```

```sh
usage-log record hook claude-event-hook source=personal-features
echo '[{"kind":"hook","name":"x"}]' | usage-log declare
```

## Concurrency

One connection per write: `busy_timeout=5000`, `BEGIN IMMEDIATE`, insert, commit,
close. WAL needs every process to share one kernel (native Docker on Linux; not a
network share or the Docker Desktop file share). A write that fails after the
timeout drops that event, prints one stderr line and returns. A skill that Claude
loads by mention, without a `Skill` tool call, is not logged.

## Test

```
uv run --package usage_log pytest libraries/usage_log/tests
```
