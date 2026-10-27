# usage_log

Standalone usage logging for the features this repo owns (skills, agents,
slash commands, hooks, odoo-mcp tools, odoo-dev commands). Python stdlib only,
Python 3.10 or later. It imports nothing from `odoo_client`, the dispatcher or the
tracker. Those call it, never the reverse.

## Switch and paths

| Item | Value |
| --- | --- |
| Root | `$ODOO_DEV_HOME` when set and not empty, else `~/.config/odoo-dev`. |
| Switch | `ODOO_DEV_USAGE_LOG=0` turns logging off. Any other value, or unset, leaves it on. |
| Job id | `ODOO_JOB_ID`, when set, is added to every usage line as `job_id` |

`usage_log` never creates the root directory. When the root does not exist,
`record`, `declare` and the CLI do nothing and the CLI exits 0. When the switch
is `0`, they do nothing too.

## Line format (the contract)

`$ODOO_DEV_HOME/usage.db`, one row per use:

```sql
usage(ts TEXT, kind TEXT, name TEXT, context TEXT)   -- index (kind, name, ts)
```

`$ODOO_DEV_HOME/declarations.db`, one row per defined feature:

```sql
declarations(kind TEXT, name TEXT, declared_at TEXT, context TEXT,
             PRIMARY KEY (kind, name))
```

- `ts` and `declared_at`: UTC ISO-8601 text, for example `2026-10-21T09:30:00.123+00:00`.
- `kind`: lower case, for example `skill`, `agent`, `command`, `hook`, `mcp_tool`,
  `sdk_command`, `job`.
- `name`: the feature name, without a leading `/`, for example `odoo-dev:plan`.
  `(kind, name)` is normalised in one place (`usage_log.normalise`), so both
  stores hold the same string.
- `context`: a JSON object as text. Any key is allowed, with no schema change.
  Usual keys: `source`, `session_id`, `agent_type`, `job_id`, `ok`, `ms`.
- Names and context only. Never tool arguments or results.
- Retention: everything is kept. There is no pruning.

## Use

```python
import usage_log
usage_log.record("skill", "odoo-dev:plan", source="odoo-dev")
usage_log.declare([{"kind": "skill", "name": "odoo-dev:plan", "context": {"source": "odoo-dev"}}])
```

```sh
usage-log record hook claude-event-hook source=personal-features
echo '[{"kind":"hook","name":"x"}]' | usage-log declare
```

## Concurrency

Every write is one connection, `busy_timeout=5000`, `BEGIN IMMEDIATE`, one
`INSERT` (or one batch of upserts), `COMMIT`, close. WAL mode is set when the
database is created and `synchronous=NORMAL` is used. Readers must keep reads
short. A write that fails after the timeout drops that one event, prints one
line to stderr and returns (exit 0). There is no retry queue or daemon.

**One-kernel constraint.** WAL needs every process to share one kernel. This
holds for containers on a Linux host with native Docker. It does not hold for
network filesystems or the Docker Desktop VM file share.

## Known gap

A skill that Claude loads on its own, by mention and without a `Skill` tool
call, is invisible to hooks and is not logged.
