---
name: odoo-upgrade
description: "Plan and run code upgrades of custom Odoo modules between major versions (16.0→17.0→18.0→19.0). Use to migrate or port a module or addon, for breaking changes, a module inventory, an upgrade estimate or plan, or the full-lifecycle SOP."
user-invocable: false
---
# Odoo Code Upgrade Guide

> **Style: caveman-compressed (durable).** This file + everything under `references/` written in caveman compression: no articles/filler/hedging, fragments OK, short synonyms. Keep style when editing — write new content compressed, don't "fix" existing text back to prose. Preserve EXACTLY: code blocks, inline code, URLs, paths, commands, technical terms, versions, numbers. Frontmatter description stays natural language (triggering). Outputs GENERATED from these files (plans, checklists, client docs) = normal prose, not caveman.

Code porting between major versions. DB upgrade (upgrade.odoo.com / odoo.sh) = human-run, never attempt.

Environment = Odoo devcontainer (load `odoo-dev:odoo-devcontainer` skill for CLI, paths, testing). Live values, injected at invocation — trust, don't re-derive:

- **Source series**: !`echo "${ODOO_VERSION:-UNKNOWN — not a devcontainer? STOP, ask the user}"`
- **Target series**: "$ARGUMENTS" — blank ⇒ take from user request. Install loop (step 7) need devcontainer running TARGET series; source ≠ target devcontainer ⇒ upgrade pass only, mark untested, escalate
- **Custom addons**: `/mnt/extra-addons` — !`find /mnt/extra-addons -mindepth 2 -maxdepth 2 -name __manifest__.py 2>/dev/null | wc -l` modules; git: !`cd /mnt/extra-addons 2>/dev/null && echo "branch $(git branch --show-current 2>/dev/null || echo none), $(git status --porcelain 2>/dev/null | wc -l) dirty files" || echo "missing"`. Override only if user say so
- **Enterprise checkouts** (`/var/lib/odoo/addons/`): !`ls /var/lib/odoo/addons/ 2>/dev/null | grep -E '^[0-9]+[.][0-9]+$' | paste -sd ' ' - || echo none`
- **gh CLI**: !`gh auth token >/dev/null 2>&1 && echo "token present" || echo "NO TOKEN — oca_check.py will fail"`

Load refs as needed — never all at once.

## When to use

A custom or OCA module has to move between major Odoo series; an upgrade project needs an inventory, a plan, or an estimate; someone asks what breaks between two versions, or what upgrade_code fixes automatically; a ported module renamed a field, model, or xmlid and now needs migration scripts; or someone asks who runs the database upgrade and what the go-live checklist looks like.

## Index

| Topic             | Load When                                                    | Ref                                                                    |
| ----------------- | ------------------------------------------------------------ | ---------------------------------------------------------------------- |
| Full-Lifecycle SOP | Plan/track whole upgrade project; anything past code (staging DB upgrade, cutover, go-live); who do what (AI vs human). Planning/communication artifact — generate outlines & checklists, never trigger execution | [sop.md](./references/sop.md) |
| Module Inventory  | Start of upgrade project; estimate/plan effort or testing; build the inventory workbook | [inventory.md](./references/inventory.md)                               |
| Prior Art (OCA + base) | Decide what NOT to port — does target standard or an OCA target module already do it. Method + scripts now live in `odoo-dev:odoo-prior-art`; inventory-column mapping stays here | [oca-base-review.md](./references/oca-base-review.md) |
| Functional Requirements | Client pare down customizations, or fresh-target-DB project — extract FR-### rows from module code | [functional-requirements.md](./references/functional-requirements.md)   |
| Porting Checklist | Port any module; workflow order + verification loop          | [porting-checklist.md](./references/porting-checklist.md)               |
| Migration Scripts | Ported module rename own field/model/xmlid                   | [migrations.md](./references/migrations.md)                             |
| On-Prem Upgrade   | Customer self-host Enterprise — run upgrade.odoo.com client, restore, merge filestore, neutralize, roll back | [on-prem-upgrade.md](./references/on-prem-upgrade.md) |
| Support Tickets   | Platform-side failure — file it, track it; blocker until resolved or waived | [support-tickets.md](./references/support-tickets.md) |
| upgrade_code Tool | Run/explain odoo-bin upgrade_code (target ≥ 18)              | [upgrade-code-tool.md](./references/upgrade-code-tool.md)               |
| 17.0 Changes      | Port code to 17.0 (from 16.0)                                | [17.0/changes.md](./references/17.0/changes.md)                         |
| 18.0 Changes      | Port code to 18.0 (from 17.0)                                | [18.0/changes.md](./references/18.0/changes.md)                         |
| 18.0 Rewrite Scripts | What Odoo 18 upgrade_code auto-fix                        | [18.0/upgrade-code-scripts.md](./references/18.0/upgrade-code-scripts.md) |
| 19.0 Changes      | Port code to 19.0 (from 18.0)                                | [19.0/changes.md](./references/19.0/changes.md)                         |
| 19.0 Rewrite Scripts | What Odoo 19 upgrade_code auto-fix                        | [19.0/upgrade-code-scripts.md](./references/19.0/upgrade-code-scripts.md) |

New Odoo version = add `references/XX.0/` folder + rows here. Folder `XX.0/` = changes **arriving in** XX.0 — what to fix when porting from previous major.

`$ODOO_DEV_STATE_DIR/upgrade-lessons/` (default `~/.local/share/odoo-dev/upgrade-lessons/`) = raw per-project lesson capture, NOT loaded during normal use. Lives outside the plugin tree because it is written during projects and a plugin update must never overwrite it. Append moment upgrade surface gotcha skill not cover (format in its README); proven lessons fold into sop.md/references at next revision, marked `[ingested]`.

## Scripts

This skill's scripts (`UPGRADE_SCRIPTS`) live at `<base directory>/scripts`, where
`<base directory>` is the absolute path on the `Base directory for this skill:`
line injected above this body. The name is a label for that directory, not a shell
variable to set and reuse: every Bash call has to spell the absolute path out in
full, because a Bash call inherits no environment and keeps no state from the call
before it.

OCA/base-review scripts **moved** to `odoo-prior-art/scripts/` (`oca_check.py`,
`oca_catalog.py`, plus new `addons_paths.sh`). Same contracts, same shared branch
cache. Load that skill for the method; this one keeps the inventory columns.

| Script                             | Run When                                                        |
| ---------------------------------- | --------------------------------------------------------------- |
| `module_inventory.py [PATH ...]`   | Start of any upgrade project — makes inventory CSV (default `/mnt/extra-addons`) |
| `studio_inventory.py [--db NAME] [--csv studio.csv]` | With inventory — enumerate Studio/UI-built artifacts (invisible to code inventory). Read-only |
| `upgrade_service.sh <test\|production> --target V (--ssh U@H --db D \| --local --dump F --contract C)` | On-prem Enterprise — drive upgrade.odoo.com client. `production` need `--yes-production` |
| `build_workbook.py --workdir DIR -o out.xlsx` | Merge seed CSV + agent JSON into the 4-sheet workbook; validates the fan-out |
| `install_all.sh [PATH]`            | Verification loop — install every module on fresh DB            |

## Rules

- **Minimum change.** Compatibility porting, not improvement: smallest diff that satisfy target version. Never refactor, restyle, or change behavior while porting.
- NEVER attempt database upgrade — humans run it (upgrade.odoo.com / odoo.sh). Same ban cover merge to production branch and modify production data; sop.md tag every lifecycle step `[AI]` or `[MANUAL]`
- Workflow (code phases): inventory (+ OCA check), then upgrade pass over all modules (upgrade_code, detection greps, manual fixes per target `changes.md`), then `install_all.sh` fresh-DB install of ALL modules, fix breakage, repeat install/fix until green. Full project sequence incl. DB/cutover phases: sop.md
- Multi-version jump: work EVERY transition ref in sequence (16→18 = 17.0 then 18.0 changes), never skip major; only major XX.0 series (16.0+) are targets, not intermediate SaaS versions. upgrade_code MAY run once across hops with target odoo-bin; install loop run only on final target devcontainer
- upgrade_code: only when target ≥ 18.0 — invocation and limits in upgrade-code-tool.md
- Bump each ported module manifest version prefix to target series (e.g. `19.0.x.y.z`)
- Inventory: scripts seed, read-only agents enrich in phases (enrich, OCA/base review, requirements), `build_workbook.py` merge. Deliverable = one xlsx, 4 sheets (Module Inventory, Functional Requirements, Traceability, Inventory Evidence), no legend/instruction sheets — column semantics live in the references. See inventory.md
- Every `native?` / `OCA alternative` / `Handled?` verdict cite a grepped path, catalog row, or fetched README in the Evidence column — never model memory of a version newer than its training
- **Identify the database before running anything.** Prod and staging shells look alike; `psql -Atc "select current_database()"` first, every time. Odoo shell auto-commit — no rollback
- **Studio is not in git.** Run `studio_inventory.py` alongside the code inventory or the UI-built half of the customization is invisible until it breaks
- Any test restore you perform yourself is NOT neutralized — `odoo-bin neutralize -d <db>` before opening it. Mail out of a test restore reach real customers