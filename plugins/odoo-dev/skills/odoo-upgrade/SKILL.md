---
name: odoo-upgrade
description: "Plan and run code upgrades of custom Odoo modules between major versions (16.0→17.0→18.0→19.0). Use to migrate or port a module or addon, for breaking changes, a module inventory, an upgrade estimate or plan, or the full-lifecycle SOP."
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
| Full-Lifecycle SOP | Plan or track the whole upgrade project; anything past code (staging DB upgrade, cutover, go-live); who does what. A planning artifact, never an execution trigger | [sop.md](./references/sop.md) |
| Module Inventory  | Start of an upgrade project; estimate or plan effort; build the inventory workbook | [inventory.md](./references/inventory.md)                               |
| Prior Art (OCA + base) | Decide what not to port; inventory-column mapping | [oca-base-review.md](./references/oca-base-review.md) |
| Functional Requirements | A client pares down customizations, or a fresh-target-DB project: extract FR-### rows from module code | [functional-requirements.md](./references/functional-requirements.md)   |
| Porting Checklist | Port any module: workflow order and the verification loop   | [porting-checklist.md](./references/porting-checklist.md)               |
| Migration Scripts | A ported module renamed its own field, model or xmlid        | [migrations.md](./references/migrations.md)                             |
| On-Prem Upgrade   | A self-hosted Enterprise customer: run the upgrade.odoo.com client, restore, merge filestore, neutralize, roll back | [on-prem-upgrade.md](./references/on-prem-upgrade.md) |
| Support Tickets   | A platform-side failure: file it, track it; a blocker until resolved or waived | [support-tickets.md](./references/support-tickets.md) |
| upgrade_code Tool | Run or explain odoo-bin upgrade_code (target 18.0 or later)  | [upgrade-code-tool.md](./references/upgrade-code-tool.md)               |
| 17.0 Changes      | Port code to 17.0 (from 16.0)                                | [17.0/changes.md](./references/17.0/changes.md)                         |
| 18.0 Changes      | Port code to 18.0 (from 17.0)                                | [18.0/changes.md](./references/18.0/changes.md)                         |
| 18.0 Rewrite Scripts | What Odoo 18 upgrade_code fixes automatically             | [18.0/upgrade-code-scripts.md](./references/18.0/upgrade-code-scripts.md) |
| 19.0 Changes      | Port code to 19.0 (from 18.0)                                | [19.0/changes.md](./references/19.0/changes.md)                         |
| 19.0 Rewrite Scripts | What Odoo 19 upgrade_code fixes automatically             | [19.0/upgrade-code-scripts.md](./references/19.0/upgrade-code-scripts.md) |

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
| `module_inventory.py [PATH ...]` | the inventory CSV (default tree `/mnt/extra-addons`) | 0 · 2 usage |
| `studio_inventory.py [--db NAME] [--ssh user@host] [--artifacts DIR] [--csv studio.csv]` | the Studio and UI-built artifacts, read-only SELECTs, through `psql` on the host with `--ssh`; outputs under `DIR/inventory/` | 0 · 2 usage or no connection |
| `upgrade_service.sh <test\|production> --target V (--ssh U@H --db D \| --local --dump F --contract C)` | drives the upgrade.odoo.com client for on-prem Enterprise; `production` needs `--yes-production` | 0 · 2 usage |
| `build_workbook.py --workdir DIR -o out.xlsx` | the 4-sheet workbook from the seed CSV and the `enrich_<module>.json` records; the last line carries `problems: N` | 0 · 1 problems found |
| `install_all.sh --db NAME [--addons-path D] [--data-dir D] [--artifacts D] [--conf F\|--no-conf] [--template [--template-db N]] [--per-tree] [--summary F] [--force] [--keep]` | the JSON summary with `trees[]`, `full`, `passed`; installs every module on a throwaway DB | 0 green · 1 an install failed · 2 usage or safety |

## Rules

- Minimum change: the smallest diff that satisfies the target series. Never refactor,
  restyle or change behaviour while porting.
- Never run a database upgrade, a merge to production, or a change to production data.
- Order: inventory, then the upgrade pass over all modules (upgrade_code, detection
  greps, manual fixes per the target `changes.md`), then `install_all.sh` until green.
- Install green is not done; tests follow.
- A multi-version jump works every transition reference in sequence. upgrade_code can run
  once across hops with the target odoo-bin; the install loop runs on the final target only.
- upgrade_code only when the target is 18.0 or later.
- Bump each ported module's manifest version prefix to the target series.
- Every verdict in the inventory cites a grepped path, a catalog row or a fetched README.
- Identify the database before you run anything: `psql -Atc "select current_database()"`.
- Studio is not in git: run `studio_inventory.py` beside the code inventory.
- A test restore you performed is not neutralized: `odoo-bin neutralize -d <db>` first.
