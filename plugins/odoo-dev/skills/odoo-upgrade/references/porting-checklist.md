# Porting Checklist

Workflow: upgrade pass over ALL modules (steps 1–6 per module, dependency/topological order from inventory CSV), then batch verification loop (step 7). One module = one commit series. **Minimum change** throughout: smallest diff that satisfy target version — never refactor, restyle, or change behavior while porting.

## 0. Preconditions

- Inventory CSV exist; module's `Upgrade action` = `keep`. (`replace` ⇒ download vendor/OCA release instead, under [Replace](#replace-obtaining-the-target-series-build) below; `merge-into-standard`/`drop` ⇒ remove from addons, note data implications for human-run DB upgrade.)
- Clean git tree, branch for target series.
- `$ODOO_VERSION` must equal TARGET series for step 7. If not: do upgrade pass anyway, mark every module **untested**, escalate — install loop need target-series devcontainer.
- Multi-hop (e.g. 16→18): upgrade_code may run ONCE across hops (target odoo-bin carry all scripts); manual work (steps 3–4) done per hop, in sequence, never skip major.
- 100+ row inventories: batch AI enrichment and porting passes in dependency-ordered chunks (context budget).

## Replace: obtaining the target-series build

Applies to every module whose `Upgrade action` is `replace` — vendor app or OCA
release. Three rules, none optional; each one is a tree that was once left
half-applied, with the old module gone, the new one never arrived, and nothing
saying so.

- **New first, old second — never the other way round.** Never remove a module
  before its replacement exists on disk AND installs on the target series. Download,
  unpack into the addons path, install, verify — *then* remove the old copy, and
  only then commit. A tree where neither copy exists cannot be recovered from the
  tree itself, and the `ir_model_data` rename migration written alongside it then
  points at a module that is not there.
- **HTML where a `.zip` was expected is a login wall, not a 404.** `apps.odoo.com`
  — and any vendor URL — serves an HTML error page to an unauthenticated
  download, so an agent cannot tell "needs login" from "does not exist". An HTML body
  on a `.zip` request is auth-gated by definition: do not retry it, do not parse it,
  do not scrape around it. Paid modules land in the same place for a second reason:
  the licence call needs a human with an account regardless.
- **Record it once, then keep going.** Emit exactly ONE human-action item in the
  completion report, naming the exact URL, exactly what to download, and the exact
  path to place it at. Mark that tree incomplete in `progress.json` — the unit's
  row becomes `failed` with a `note` naming the login wall, which is what
  "incomplete" means in that four-word vocabulary — and continue with the other
  trees. One blocked download blocks one module, never the pass; a blocked module
  that nothing records is the failure these rules exist to prevent.

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

- Record the module's findings as an `enrich_<module>.json` record in the inventory work dir (raise Complexity, flag follow-ups) — never edit the seed CSV in place; the CSV is a seed and `build_workbook.py` reads the records, not an enriched CSV. Keys: [inventory.md](./inventory.md). Flip the module's `progress.json` row to `done`. Commit; next module in topological order.
- Client use Studio? Flag modules with changed views as elevated risk — Studio views layer on custom views, break on DB upgrade; warn human running it.

## 7. Verification loop (all modules, target devcontainer)

- `install_all.sh` — drop dev DB, install EVERY custom module on fresh one. Must exit clean: no tracebacks, no "invalid view" warnings.
- Fix what break (minimal diffs, commit per module), rerun. Repeat until green.
- Then: run test suites with target odoo-bin (`odoo-bin test`), smoke-test main views (form/list/kanban) + report rendering — green install alone prove little; views can install yet render broken.
- Upgrade path: `-u <module>` on DB where previous version was installed, when available.