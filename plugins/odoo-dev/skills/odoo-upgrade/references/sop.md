# SOP — Odoo Major-Version Upgrade (Full Lifecycle)

**Purpose.** Standardize full upgrade of customized Odoo database + custom-module codebase from one major version to later major, project start through production go-live and verification.

**Use.** Planning and communication artifact: produce project outline, phase checklists, estimates, status tracking for client review. Reading authorizes no execution — perform step only when user explicitly asks, and only if tagged `[AI]`.

**Scope.** Any 16.0-or-later major series, any later major target. Primary procedure: odoo.sh hosting. On-premise (upgrade.odoo.com) and Odoo Online divergences appear as *Variant* notes at relevant step. Business/functional acceptance testing out of scope — technical procedure only.

**Prerequisites.**

- Devcontainer running TARGET series (code phases) — see `odoo-dev:odoo-devcontainer` skill.
- Custom-addons git repo access; `gh` authenticated (`oca_check.py`).
- odoo.sh project access with staging branch (DB phases) — human-held.
- Enterprise source checkouts for source + target series (diffing).

**Performer tags.** Each step carries exactly one tag:

- `[AI]` — AI executes (human review gate stated in step).
- `[MANUAL]` — human performs. AI never performs these, even with technical access; may prepare commands, scripts, checklists for them.

Standing rule throughout: AI never triggers database upgrade, never merges to production branch, never modifies production data.

**Identify the database before every command that writes.** Production and staging shells differ only by an opaque build id, and odoo.sh colours production red and staging yellow — colour is a poor guard for a pasted command. Run `psql -Atc "select current_database()"` first, every time. In an Odoo shell console transactions **commit automatically**; in psql, wrap writes in `BEGIN` / `COMMIT` / `ROLLBACK`. Any test restore you performed yourself is NOT neutralized — `odoo-bin neutralize -d <db>` before opening it, or its mail reaches real customers.

**Definitions.**

- *Staging branch* — odoo.sh branch running neutralized copy of production database for testing.
- *Production branch* — single odoo.sh branch whose pushes deploy to live database.
- *Update-on-commit mode* — branch state after upgrade request: every push restores upgraded backup and updates all custom modules on it.
- *Neutralization* — platform disables outgoing effects on non-production databases: scheduled actions off, mail intercepted, payment/shipping in test mode, bank sync off.
- *Migration script* — Python in `<module>/migrations/<manifest-version>/`, executed by phase when module updates across that version: `pre-` before module loads, `post-` after, `end-` after ALL modules updated.
- *Downgrade trick* — set `ir_module_module.latest_version` below manifest version so migration scripts re-fire on next module update; used for testing.
- *Baseline* — pinned source-series worktree/database state later diffs and audits compare against.

---

## Procedure

### Phase 1 — Freeze and baseline

1.1 `[MANUAL]` Declare development freeze on custom codebase (bug fixes exempt). Every feature merged after must be ported and re-verified separately.
1.2 `[AI]` Pin baseline: check out source-series merge-base as side-by-side git worktree. All later "what changed" claims must diff against this baseline.

### Phase 2 — Inventory and classification

2.1 `[AI]` Run `module_inventory.py` for inventory CSV; fill `TODO-AI` cells from module code (see [inventory.md](./inventory.md)).
2.2 `[AI]` Run `oca_check.py` (now in `odoo-prior-art/scripts/`) against inventory. Author strings lie: module counts as OCA-maintained only when scan finds it in OCA repo for target series.
2.2b `[AI]` Run `studio_inventory.py --db <production-copy> --csv studio.csv` — read-only. Studio and other UI-built customization lives in database rows, not the repo, so the code inventory cannot see any of it. Classify each row purge / convert-to-code / keep-as-data; `convert-to-code` rows are scope, and each needs a real module plus a migration script to carry its column (see [inventory.md](./inventory.md), [migrations.md](./migrations.md)). Run against a COPY, never production.
2.3 `[AI]` Record verdict per module — *keep* (port), *replace* (official target-series build exists), or *drop* — each with one-line evidence-based rationale. Challenge each *keep* against features newly standard in target version (release notes): redundancy with standard = grounds for *drop*/*replace*. Human approves verdict list before any porting starts.
2.4 `[AI]` Build dependency graph; sort *keep* modules into porting waves.
2.5 `[MANUAL]` Purchase/download target-series builds of paid vendor modules. No port work on vendor module until official build ruled out.
2.6 `[MANUAL]` Request initial upgraded test database now, parallel with code port (official recommendation): proves standard upgrade passes on real data before custom work depends on it, surfaces platform failures early.
2.7 `[AI]` Audit production database for objects created outside modules (hand-made SQL views, triggers, columns). Upgrade platform drops them; anything still needed must move into module-owned code before phase 6.

### Phase 3 — Code port (per wave)

3.1 `[AI]` Mechanical rewrite: run `upgrade_code` (target ≥ 18.0), scoped with `--glob` — see [upgrade-code-tool.md](./upgrade-code-tool.md).
3.2 `[AI]` Semantic port: fix what rewriter cannot, using target-series `changes.md` for EVERY intermediate major in sequence (never skip hop). Minimum change only; follow [porting-checklist.md](./porting-checklist.md).
3.3 `[AI]` Bump each ported manifest to target series prefix. Migration script folders must sit at exactly manifest version (`migrations/<manifest-version>/`) — folder above manifest version silently skipped by MigrationManager.
3.4 `[AI]` Verification loop: `install_all.sh` fresh-DB install of ALL modules; fix; repeat until zero tracebacks. Then run module test suites on target series.

### Phase 4 — Stored-data audit and migration scripts

4.1 `[AI]` Audit every ported module's full baseline-to-port diff against 7-point stored-data checklist (see [migrations.md](./migrations.md)).
4.2 `[AI]` Write migration scripts ONLY where module-owned stored data changes shape; core-owned data belongs to platform upgrade, never module scripts. Use official `upgrade-util` helpers (`rename_field`, `rename_model`, `rename_xmlid`, `remove_module`, …) or OCA `openupgradelib` over hand-rolled SQL where helper exists. Consult OCA OpenUpgrade per-module `upgrade_analysis` files (github.com/OCA/OpenUpgrade, `openupgrade_scripts`) — they enumerate every core model/field change between the two majors, catch renames manual diff misses.
4.2b `[AI]` Custom records flagged `noupdate` NOT refreshed by module update: where target-version content must change, update from migration script (`update_record_from_xml()` / `convert_file(mode='init')`).
4.3 `[AI]` Prove every script idempotent: downgrade trick (`UPDATE ir_module_module SET
    latest_version = '<previous>'` + `-u module`) run twice on target-series DB — second run must change nothing.

### Phase 5 — Module removals (modules classified *drop*)

5.1 `[AI]` Decouple: remove all code dependencies on dropped modules in change that installs and passes on SOURCE series. Deploy to production first.
5.2 `[MANUAL]` On production, before any upgrade: drain dropped modules' pending data (e.g. job queues), then uninstall them. Skipping this leaves orphaned "installed" modules with code gone — every registry load errors after upgrade.
5.3 `[AI]` Delete module code from repo only after production uninstall confirmed.

### Phase 6 — Staging database upgrade (odoo.sh)

6.1 `[MANUAL]` Activate upgrade on staging branch (Upgrade tab, target version). Platform sends latest production daily backup to upgrade service, enters update-on-commit mode: every push restores upgraded backup and updates all custom modules — including migration scripts.
6.2 `[AI]` Push ported code; read `~/logs/upgrade.log` and `~/logs/odoo.log` on build. Triage EVERY error line: (a) code defect — fix and push, (b) platform/standard-phase noise — document evidence, (c) platform defect — escalate per 6.6. Exit criterion: zero unexplained ERROR lines.
6.3 `[AI]` Audit views deactivated by upgrade. Platform validates views before custom modules load, disables failures; later module update rewrites arch but never restores `active` flag. Diff inactive-view set against pre-upgrade baseline (keyed on view id), test each candidate with savepoint `write(active=True)` + `_check_xml()`, then ship `end-` migration script keyed on xmlids reactivating validated set (skip-and-log on failure). Views whose arch is client data (Studio) and no longer validates stay inactive, routed to functional review.
6.4 `[AI]` Review upgrade report (Discuss, Administration/Settings users) and platform's disabled-view/changed-data notes; fold findings into 6.2/6.3.
6.5 `[AI]` Repeat 6.2–6.4 on fresh rebuilds (each starts from new production dump) until rebuild completes with only documented, explained log noise.
6.6 `[MANUAL]` Report every platform-side failure (e.g. false-positive upgrade invariant) to Odoo support as "an issue related to my future upgrade". Pending platform issue = go-live blocker until resolved or waived in writing.
6.6b `[AI]` Record every such ticket in the project `tickets.csv` register (id, date, subject, token, status, blocking_module, resolution, link) — template, subject format and body in [support-tickets.md](./support-tickets.md). Quote the upgrade **token**, not a request id: the token is the only identifier the client prints and the only one support can use. Support is NOT a client surface — full traceback belongs there, unlike PR bodies and chatter. `build_workbook.py --tickets tickets.csv` folds the register into the workbook.

Note: test/staging databases neutralized by platform — scheduled actions disabled, outgoing mail intercepted, payment providers/shipping connectors in test mode, bank sync off. Not upgrade defects; testing integration needs sandbox credentials.

*Variant — on-premise (Enterprise):* full runbook in [on-prem-upgrade.md](./on-prem-upgrade.md); `scripts/upgrade_service.sh` wraps both shapes (SSH to the customer server, or locally against a fetched dump).
```bash
<base directory>/scripts/upgrade_service.sh test --ssh <user@host> --db <db> --target <target>
```
The copy is submitted **without a filestore**, so the returned `filestore/` must be MERGED into production's — and the client's own `restore` subcommand never performs that merge. Then `-u <your custom modules>` (named, not `all`). Neutralize any restore before opening it. The service identifier is a **token**, not a request id; capture it for 6.6b.
*Variant — on-premise (Community):* upgrade.odoo.com serves Enterprise databases only. Use OCA OpenUpgrade: run target-series server with
`--upgrade-path=<openupgrade_scripts>/scripts --load=base,web,openupgrade_framework -u all`
on copy, one major at a time; openupgradelib backs scripts. Same testing and iteration criteria as 6.2–6.5.
*Variant — Odoo Online:* no custom Python modules; request test database from database manager, skip 6.2 module iteration.

### Phase 7 — Pre-production checklist

Complete every item before scheduling go-live:

7.1 `[MANUAL]` All prerequisite changes merged and deployed to production (phase 5 decoupling, production uninstalls done).
7.2 `[MANUAL]` All 6.6 support tickets resolved or waived.
7.3 `[AI]` Latest staging rebuild verified clean (6.5 criterion) — staging upgrade status must be *successful*; platform requires before production.
7.4 `[MANUAL]` Custom domains verified as CNAME records (database IP can change during upgrade; bare-domain A records break).
7.5 `[MANUAL]` Freeze window declared: no merges to production branch between final rehearsal and go-live, or every such merge same-day ported and verified on upgrade branch.
7.6 `[MANUAL]` Downtime window scheduled at minimal usage; database unavailable for full upgrade duration (measure from staging runs).
7.7 `[AI]` Day-before rehearsal: trigger criterion human, verification AI — fresh staging rebuild from newest production dump, verified per 6.5.

### Phase 8 — Production cutover (odoo.sh)

8.1 `[MANUAL]` Activate upgrade on production branch (Upgrade tab).
8.2 `[MANUAL]` Merge upgrade branch into production branch. Merge IS trigger: platform synchronizes database upgrade with deployment of upgraded code. Push nothing else to production branch after 8.1.
8.3 `[MANUAL]` Monitor. Database unavailable throughout. On failure platform reverts automatically; on success it stores pre-upgrade backup and upgrade is irreversible.

*Variant — on-premise:* `upgrade_service.sh production ... --yes-production` (it exits 7 without that flag, and the step stays `[MANUAL]` regardless). Modifications made after the upload are lost — stop using the database first. Once complete it is impossible to revert: rollback is your verified pre-upgrade backup **plus** reverting the code, both together. Odoo expects the production run within 3 days of the test run. Procedure: [on-prem-upgrade.md](./on-prem-upgrade.md).

### Phase 9 — Post-upgrade verification

9.1 `[AI]` `upgrade.log`: every migration script from phase 4 and 6.3 executed; no unexplained ERROR/CRITICAL.
9.2 `[AI]` Module states: no `to upgrade`/`to install`/orphaned rows in `ir_module_module`.
9.3 `[AI]` Sentinel data checks: row counts on custom SQL views/BI models, one known record per critical model vs expectation.
9.4 `[AI]` Runtime liveness: crons enabled and firing, outgoing mail configured, integrations authenticating. Fix is `[MANUAL]` if it touches production data.
9.5 `[MANUAL]` Confirm pre-upgrade backup exists and is restorable before declaring upgrade complete.
9.6 `[MANUAL]` Report any production-upgrade defect to Odoo support under "an issue related to my upgrade (production)" — distinct post-go-live support channel.

---

## Quality control

Invariants define "done" per phase; do not proceed past phase while one fails:

- Fresh-DB install of ALL modules: zero tracebacks (phase 3).
- Every migration script proven idempotent by two consecutive runs (phase 4).
- Staging rebuild with zero unexplained ERROR lines (phase 6).
- Staging upgrade status *successful* before any production request (phase 7).
- Post-upgrade sentinel checks pass (phase 9).

Living document: when upgrade project surfaces lesson SOP not cover, record in `$ODOO_DEV_STATE_DIR/upgrade-lessons/` (default `~/.local/share/odoo-dev/upgrade-lessons/`) during project (raw capture, one file per project); fold proven lessons into SOP and reference files at next revision.

## References

1. Odoo — Upgrade (administration):
   https://www.odoo.com/documentation/19.0/administration/upgrade.html
2. Odoo — Upgrade a customized database (developer how-to):
   https://www.odoo.com/documentation/19.0/developer/howtos/upgrade_custom_db.html
3. Odoo — Odoo.sh branches:
   https://www.odoo.com/documentation/19.0/administration/odoo_sh/getting_started/branches.html
4. Odoo — upgrade-util helpers: https://github.com/odoo/upgrade-util
5. OCA — OpenUpgrade (analysis files, openupgrade_framework/scripts):
   https://github.com/OCA/OpenUpgrade — overview: https://www.odoo-community.org/about/openupgrade
6. Skill-internal: [inventory.md](./inventory.md), [porting-checklist.md](./porting-checklist.md),
   [migrations.md](./migrations.md), [upgrade-code-tool.md](./upgrade-code-tool.md),
   per-version `XX.0/changes.md`.