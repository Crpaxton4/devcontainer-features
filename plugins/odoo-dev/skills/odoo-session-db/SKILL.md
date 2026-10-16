---
name: odoo-session-db
description: "Give one session its own throwaway test database name, so two sessions on one task in one project cannot land on the same database. Use before odoo-dev:odoo-test-run whenever more than one session can work in the same project, and when a test database vanished mid-run."
user-invocable: false
---
# Odoo Session DB

Wrap `odoo-dev:odoo-test-run` so each session runs on a database no other session can name.

`run-tests.sh` names its database `<project>_test_<task_id><suffix>` and runs
`dropdb --if-exists` on that name before it creates it. Postgres is one shared
server for every container on the machine. Two sessions on one task in one project, with
no suffix, share one name, and the second drops the first one's database mid-run.

This skill adds a suffix that is unique to the session. It adds no naming scheme.
The name stays `<project>_test_<task_id><suffix>`, and `run-tests.sh` still creates and
drops it.

## Run the tests

```bash
<base directory>/scripts/session-db.sh run <repo> <task_id> <module> \
  [--session-id ID] [any run-tests.sh option except --db-suffix]
```

→ the result JSON of `run-tests.sh`, unchanged. Read `status` as in `odoo-dev:odoo-test-run`.

- Pass the same `--session-id` on every call in a session. The suffix is a hash of it,
  so the name is the same for the whole session. An empty value exits 2.
- `SESSION_DB_ID` works the same way, but an exported value is inherited by every
  parallel subagent. That gives them all one suffix and brings the collision back.
  Each session must set its own id. Prefer `--session-id`.
- The result JSON is always printed, also when the run fails. The script then exits with
  the exit code of `run-tests.sh`. `series_mismatch` creates no database, so it is not
  an exit 3.
- `task_id` must match `[0-9A-Za-z_-]+`.
- With no session id the suffix is random for each call. That is still unique.
- The script exits 3 if the database `run-tests.sh` reports is not the database named
  here, or was not dropped. Treat exit 3 as a failed run. Do not retry on the same name.
- Do not pass `--db-suffix`. The script sets it and refuses yours.

## Read a name or a suffix

```bash
<base directory>/scripts/session-db.sh name <repo> <task_id> [--session-id ID]
<base directory>/scripts/session-db.sh suffix [--session-id ID]
```

`name` prints the database for this session. `suffix` prints `_s` and 8 hex digits.
`name` exits 2 when the name is over the Postgres limit of 63 bytes, because Postgres
cuts longer names without a warning.
