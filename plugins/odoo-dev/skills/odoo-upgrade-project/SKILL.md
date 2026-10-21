---
name: odoo-upgrade-project
description: "Human SOP for a whole Odoo upgrade project past the code port: staging database upgrade through upgrade.odoo.com, on-prem restore, cutover, go-live, support tickets and functional requirements. A planning reference."
user-invocable: false
---
# Odoo Upgrade Project

The human SOP for an Odoo upgrade project past the code port. This skill is a planning
reference that the `odoo-dev-upgrader` agent does not preload. The database upgrade is
human-run.

## Index

| Topic | Load When | Ref |
| --- | --- | --- |
| Full-Lifecycle SOP | Plan or track the whole upgrade project; anything past code (staging DB upgrade, cutover, go-live); who does what. A planning artifact, never an execution trigger | [sop.md](./references/sop.md) |
| Functional Requirements | A client pares down customizations, or a fresh-target-DB project: extract FR-### rows from module code | [functional-requirements.md](./references/functional-requirements.md) |
| On-Prem Upgrade | A self-hosted Enterprise customer: run the upgrade.odoo.com client, restore, merge filestore, neutralize, roll back | [on-prem-upgrade.md](./references/on-prem-upgrade.md) |
| Support Tickets | A platform-side failure: file it, track it; a blocker until resolved or waived | [support-tickets.md](./references/support-tickets.md) |

## Scripts

Write every path in full: `<base directory>/scripts/<script>`.

| Script and arguments | Prints | Exit |
| --- | --- | --- |
| `upgrade_service.sh <test\|production> --target V (--ssh U@H --db D \| --local --dump F --contract C)` | drives the upgrade.odoo.com client for on-prem Enterprise; `production` needs `--yes-production` | 0 · 2 usage |
