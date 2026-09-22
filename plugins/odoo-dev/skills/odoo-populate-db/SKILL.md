---
name: odoo-populate-db
description: "Seed a local Odoo database from a named model profile (business, sales, inventory, accounting, all), printing the resolved model order first. Use to populate or seed a dev or benchmark database, and when odoo populate exited 0 having silently skipped most models."
user-invocable: false
---
# Odoo Populate DB

Seed a local database from a named profile instead of a hand-typed `--models`
list, and **fail when the population fails**. `odoo populate` cannot report a
failure; this wrapper exists to do it for it.

## When to use

A local or benchmark database needs realistic volume; someone hand-writes a long
`odoo populate --models=...` line; a populate run "succeeded" but most models are
empty; a model's factory collides with a custom addon and the run has to go ahead
without it; or two databases with identical data are needed for an A/B benchmark.

## Why this exists

`odoo/cli/populate.py` wraps its whole model loop in a bare `except:`, logs
`Something went wrong populating database` with the traceback, and **returns
normally**. The process exits 0. Every model after the failing one is skipped
silently, so a thirty-minute run ends with a half-empty database and a shell that
saw success. Three smaller versions of the same problem ride along:

- `--models` is a flat comma list. Getting it wrong is discovered deep into the
  run, not before it.
- `_get_ordered_models` pulls in each model's `_populate_dependencies` behind the
  scenes, so the set that runs is never the set you asked for, and the only way to
  learn what it was is to read the log afterwards.
- Nothing says how many rows landed. Confirming that means writing the SQL.

So this skill never reads `$?`. It reads the log, names the model that raised,
prints the traceback the CLI discarded, and **exits 4**.

## Preconditions

Run this **inside** the stack, where `odoo` and `psql` are both on PATH — bring
one up with **`odoo-dev:odoo-task-env`** if there is none. There is deliberately
no docker-exec path: a populate run is an act against one named long-lived
database you already own a shell in, not a throwaway a gate creates. The database
must already exist; this script never creates or drops one.

## Scripts

This skill's scripts (`POPULATE_SCRIPTS`) live at `<base directory>/scripts`,
where `<base directory>` is the absolute path on the `Base directory for this
skill:` line injected above this body. The name is a label for that directory, not
a shell variable to set and reuse: every Bash call has to spell the absolute path
out in full, because a Bash call inherits no environment and keeps no state from
the call before it.

```bash
<base directory>/scripts/populate-db.sh --database <db> \
  [--profile business|sales|inventory|accounting|all] \
  [--size small|medium|large] [--exclude <model>]... [--dry-run]
```

→ ```json
{"ok":true,"database":"qoc_bench","profile":"business","size":"medium",
 "dry_run":false,"excluded":[],"excluded_dependents":[],"excluded_unknown":[],
 "dependency_added":["res.currency"],"unavailable":["sale.order"],
 "models":[{"model":"res.partner","table":"res_partner","requested":true,
   "factories":true,"rows_before":2,"rows_after":102,"rows_added":100,"seconds":6}],
 "models_planned":5,"models_started":5,"models_not_started":[],
 "failed_model":null,"traceback":null,"elapsed_seconds":812,"exit_code":0,
 "log_file":"/tmp/populate-db.XXXX.log"}
```

Human-readable output — the resolved order, the row-count table, the failure
report — goes to **stderr**, so the last line of stdout is always the JSON.

Exit codes: `0` every planned model populated · `2` usage or environment · `3` the
model order could not be resolved · `4` the run aborted mid-way, or `odoo populate`
itself failed. **`4` is the one this skill was written for**, and it is the code
the bare `except:` would otherwise have turned into `0`.

## Profiles

Curated model names, so nobody types the list again. Dependencies are never listed
in a profile — they are resolved from the live registry on top of it.

| Profile | Covers |
|---|---|
| `business` | partners, users, products, sales orders, pickings and moves, journal entries — the issue's original hand-typed list |
| `sales` | partners, products, `sale.order` and its lines |
| `inventory` | products, warehouses, locations, quants, pickings and moves |
| `accounting` | partners, accounts, journals, `account.move` and its lines |
| `all` | every model that overrides `_populate_factories`, i.e. everything that can actually generate rows |

`--size` is passed **straight through** to `odoo populate`. The `_populate_sizes`
tiers are the CLI's contract; nothing here tries to hit a specific row count.

## The resolved order, before anything is written

Every run prints the order it is about to use, and `--dry-run` prints it and stops.
The order comes from `odoo shell` walking the real registry's
`_populate_dependencies` in the same post-order `_get_ordered_models` uses — it is
read off the database being populated, never a table hard-coded in the script.

```
resolved model order (5 models, --size medium):
    1. res.currency   [dependency, ~10 rows]
    2. res.partner   [~100 rows]
    ...
asked for but not in this database (module not installed?): sale.order
```

`[dependency]` marks a model you did not ask for and will get anyway — the thing
that was previously only visible in the log. `[no factories, makes nothing]` marks
one that will be visited and produce nothing. `unavailable` names a profile entry
this database has no model for, which usually means the module is not installed.

## Dropping a model whose factory conflicts

```bash
<base directory>/scripts/populate-db.sh --database qoc_bench --profile business \
  --exclude account.move --dry-run
```

**`--exclude` takes the excluded model's dependents with it, transitively.**
`--models` does not suppress a dependency: anything reached through another
model's `_populate_dependencies` is populated whether or not it is in the list, so
excluding `account.move` while `account.move.line` stayed would populate
`account.move` anyway and the exclusion would be a lie. Everything dropped for that
reason is named on stderr and in `excluded_dependents`, so the cost of the
exclusion is visible before the run rather than after it.

## A/B benchmarking: populate once, then duplicate

Benchmarking a change needs two databases holding the *same* data. Do not populate
twice.

```bash
<base directory>/scripts/populate-db.sh --database bench_a --profile business --size medium
createdb -T bench_a bench_b     # template copy: identical rows, identical ids
```

`odoo populate` draws from randomised factories on every run, so two runs at the
same `--size` give two *different* databases — a comparison between them measures
the data as much as it measures the code. A template copy is exact, and it costs
seconds against the half-hour the second populate would have cost.

Postgres refuses `createdb -T` while any session is connected to the template, so
stop the Odoo processes on `bench_a` first. The copy also carries `bench_a`'s
`database.uuid` and its scheduled actions; neutralize it before pointing anything
outbound at it.

## Reading the result

| Field | Rule |
|---|---|
| `ok` | The verdict. **Never `$?` from `odoo populate`** — that is 0 even when the run aborted |
| `failed_model` | The model the swallowed exception came from: the last one the run announced before the error |
| `traceback` | What the bare `except:` logged and then discarded. Re-run with `--exclude <failed_model>` to get past it |
| `models_not_started` | Planned, factory-bearing, and never reached. This is the aborted tail |
| `models_started` | Zero is never a pass, whatever the exit code says — an unparsed log also reports 0, so the parse fails closed |
| `rows_added` | `rows_after - rows_before` per model. The only honest count: a database is rarely empty to begin with, and the CLI throws away the ids it created |
| `seconds` | Per model, from the log's own timestamps |

`log_file` is kept on purpose and is not cleaned up — the traceback is the reason
anyone opens it.

## Non-goals

- **Resumability.** `odoo populate` initialises `registry.populated_models` empty
  on every invocation and has no concept of partial state. Rebuilding that in a
  wrapper is more complexity than it is worth; re-run with `--exclude` instead.
- **Custom row-count targets.** `_populate_sizes` is the CLI's contract.
- **Any change to the `odoo populate` API.** This curates the list and reports the
  result. It is a convenience wrapper, not a replacement.

## Environment knobs

| Variable | Default | Why |
|---|---|---|
| `ODOO_BIN` | `odoo` | The Odoo entry point, when it is not on PATH under that name |
| `POPULATE_DB_ROLE` | unset | Postgres role for the row counts; unset uses libpq's own `PGUSER` |

## Verify

```bash
bash <base directory>/scripts/tests/populate-db.test.sh   # offline: stubbed odoo and psql, canned logs, real parsing
```
