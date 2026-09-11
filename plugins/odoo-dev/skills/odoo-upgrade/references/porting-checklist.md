# Porting Checklist

Workflow: upgrade pass over ALL modules (steps 1–6 per module, dependency/topological order from inventory CSV), then batch verification loop (step 7). One module = one commit series. **Minimum change** throughout: smallest diff that satisfy target version — never refactor, restyle, or change behavior while porting.

## 0. Preconditions

- Inventory CSV exist; module's `Upgrade action` = `keep`. (`replace` ⇒ download vendor/OCA release instead; `merge-into-standard`/`drop` ⇒ remove from addons, note data implications for human-run DB upgrade.)
- Clean git tree, branch for target series.
- `$ODOO_VERSION` must equal TARGET series for step 7. If not: do upgrade pass anyway, mark every module **untested**, escalate — install loop need target-series devcontainer.
- Multi-hop (e.g. 16→18): upgrade_code may run ONCE across hops (target odoo-bin carry all scripts); manual work (steps 3–4) done per hop, in sequence, never skip major.
- 100+ row inventories: batch AI enrichment and porting passes in dependency-ordered chunks (context budget).

## 1. Manifest

- Bump `version` prefix to target series: `19.0.x.y.z`. Keep module's own `x.y.z` tail.
- Check `depends`: renamed/merged/removed in target? Core module renames/merges live in OpenUpgrade's `openupgrade_scripts/apriori.py` (branch = target series) — check before hand-researching.
- Check `external_dependencies` still importable on target's Python floor.

## 2. Automated rewrite (target ≥ 18.0 only)

- Run TARGET version's `odoo-bin upgrade_code` (`--dry-run` first) — see [upgrade-code-tool.md](./upgrade-code-tool.md); target's `XX.0/upgrade-code-scripts.md` list what it fix.
- Review diff hunk by hunk. Commit mechanical rewrite separate from manual fixes.
- Target 17.0: no core tool — OCA `odoo-module-migrator` offer partial automation (see 17.0/changes.md).

## 3. Detection greps

- Run `Detection greps` block from target's `changes.md` in module dir. Every hit = work item. Empty grep output ≠ done — greps catch renames, not semantic changes.

## 4. Manual fixes

Work target `changes.md` top to bottom: Manifest → Python/ORM → Views/XML → JS/Owl/Assets → Hooks & misc.

- Core-field renames not in `changes.md`: check OpenUpgrade analysis file for each model module touch (path pattern in changes file's Sources).
- Views inheriting core views: verify every xpath against target version's actual view arch, not memory.
- Renamed module's OWN fields/models/xmlids during port? Existing DBs need `migrations/` script — see [migrations.md](./migrations.md).
- Grep OTHER custom modules for `inherit_id` references to this module's views — xpath breakage cascade to siblings.

## 5. i18n

- Source terms moved/changed (tree→list, attrs→expressions): regenerate module `.pot`, `msgmerge` the `.po` files so existing translations follow.

## 6. Wrap up module

- Update inventory CSV row (raise Complexity, flag follow-ups). Commit; next module in topological order.
- Client use Studio? Flag modules with changed views as elevated risk — Studio views layer on custom views, break on DB upgrade; warn human running it.

## 7. Verification loop (all modules, target devcontainer)

- `install_all.sh` — drop dev DB, install EVERY custom module on fresh one. Must exit clean: no tracebacks, no "invalid view" warnings.
- Fix what break (minimal diffs, commit per module), rerun. Repeat until green.
- Then: run test suites with target odoo-bin (`odoo-bin test`), smoke-test main views (form/list/kanban) + report rendering — green install alone prove little; views can install yet render broken.
- Upgrade path: `-u <module>` on DB where previous version was installed, when available.