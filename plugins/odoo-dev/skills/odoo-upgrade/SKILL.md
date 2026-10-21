---
name: odoo-upgrade
description: "Plan and run code upgrades of custom Odoo modules between major versions (16.0→17.0→18.0→19.0). Use to migrate or port a module or addon, for breaking changes, a module inventory, or an upgrade estimate or plan."
user-invocable: false
---
# Odoo Code Upgrade Guide

Code porting between major series. The database upgrade is human-run; never attempt it.

Inputs: the source series is `ODOO_VERSION` of this shell; the target series comes from
the request. `preflight.sh --target-series <target>` checks the target series.

Load references as needed, never all at once.

## Index

| Topic             | Load When                                                    | Ref                                                                    |
| ----------------- | ------------------------------------------------------------ | ---------------------------------------------------------------------- |
| Upgrade project SOP | Anything past the code port: planning, staging DB upgrade, cutover, tickets, functional requirements | the `odoo-dev:odoo-upgrade-project` skill |
| Module Inventory  | Start of an upgrade project; estimate or plan effort; build the inventory workbook | [inventory.md](./references/inventory.md)                               |
| Prior Art (OCA + base) | Decide what not to port; inventory-column mapping | [oca-base-review.md](./references/oca-base-review.md) |
| Porting Checklist | Port any module: workflow order and the verification loop   | [porting-checklist.md](./references/porting-checklist.md)               |
| Migration Scripts | A ported module renamed its own field, model or xmlid        | [migrations.md](./references/migrations.md)                             |
| upgrade_code Tool | Run or explain odoo-bin upgrade_code (target 18.0 or later)  | [upgrade-code-tool.md](./references/upgrade-code-tool.md)               |
| 17.0 Changes      | Port code to 17.0 (from 16.0)                                | [17.0/changes.md](./references/17.0/changes.md)                         |
| 18.0 Changes      | Port code to 18.0 (from 17.0)                                | [18.0/changes.md](./references/18.0/changes.md)                         |
| upgrade_code Scripts | What each upgrade_code script rewrites, by the series that ships it | [upgrade-code-scripts.md](./references/upgrade-code-scripts.md) |
| 19.0 Changes      | Port code to 19.0 (from 18.0)                                | [19.0/changes.md](./references/19.0/changes.md)                         |

A new Odoo version adds a `references/XX.0/` folder and rows here. Folder `XX.0/` holds
the changes that arrive in XX.0.

`$ODOO_DEV_STATE_DIR/upgrade-lessons/` (default `~/.local/share/odoo-dev/upgrade-lessons/`)
is the raw per-project lesson capture, outside the plugin tree and not loaded in normal
use. Append a gotcha the skill does not cover (format in its README). Proven lessons
fold into the references at the next revision, marked `[ingested]`.

## Scripts

Write every path in full: `<base directory>/scripts/<script>`.

| Script and arguments | Prints | Exit |
| --- | --- | --- |
| `upgrade-preflight.sh --target V --project "<name>" [--ssh H] [--repo-path DIR]` | one `PASS`/`FAIL` line per check on stderr, then one JSON line: `ok`, `failures[]`, `source_db`, `enterprise_path`, `repo`, `repo_path`, `default_branch`, `env_fingerprint` | 0 ok · 10 a check failed · 2 usage |
| `module_inventory.py [PATH ...] [-o FILE]` | writes the inventory CSV to `-o` (default `./inventory.csv`; default tree `/mnt/extra-addons`) | 0 · 1 no module found · 2 usage |
| `studio_inventory.py [--db NAME] [--ssh user@host] [--artifacts DIR] [--csv studio.csv]` | the Studio and UI-built artifacts, read-only SELECTs, through `psql` on the host with `--ssh`; outputs under `DIR/inventory/` | 0 · 2 usage or no connection |
| `build_workbook.py --workdir DIR -o out.xlsx` | the 4-sheet workbook from the seed CSV and the `enrich_<module>.json` records; the last line carries `problems: N` | 0 · 1 problems found |
| `install_all.sh --db NAME --addons-path D [--enterprise D] [--data-dir D] [--artifacts D] [--conf F\|--no-conf] [--template] [--per-tree] [--summary F] [--expect modules.json] [--force]` | the JSON summary with `status`, `series`, `trees[]` (each with `class` env or port), `full`, `env_failure`, `skipped[]` (`{module, reason}`: `installable: False`, a manifest version not in the target series, or a dependency on a skipped module; each also prints `SKIP <module>: <reason>` on stderr), `expected_but_skipped[]` (modules named by `--expect` that were skipped; the run then exits 1), `passed`, written on every exit; installs every module on a throwaway DB. The test suite passes `--no-conf`; a real run auto-loads `$ODOO_RC`, `/etc/odoo/odoo.conf` or `~/.odoorc` | 0 green · 1 a port defect · 2 usage or safety · 10 environment |

## Rules

- Minimum change: the smallest diff that satisfies the target series. Never refactor,
  restyle or change behaviour while porting.
- Never run a database upgrade, a merge to production, or a change to production data.
- Order: inventory, then the upgrade pass over all modules (upgrade_code, detection
  greps, manual fixes per the target `changes.md`), then `install_all.sh` until green.
- A multi-version jump works every transition reference in sequence. upgrade_code can run
  once across hops with the target odoo-bin; the install loop runs on the final target only.
- upgrade_code only when the target is 18.0 or later.
- Bump each ported module's manifest version prefix to the target series.
- Every verdict in the inventory cites a grepped path, a catalog row or a fetched README.
- Identify the database before you run anything: `psql -Atc "select current_database()"`.
- Studio is not in git: run `studio_inventory.py` beside the code inventory.
- A test restore you performed is not neutralized: `odoo-bin neutralize -d <db>` first.
