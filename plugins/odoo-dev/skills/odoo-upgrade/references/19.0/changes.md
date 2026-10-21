# Breaking changes arriving in Odoo 19.0
Verified against sources 2026-07.

Port direction: 18.0 -> 19.0. This file lists the items that need judgment. `scripts/port-module.sh <module> --to 19.0 --worktree <W>` applies the mechanical rewrites first (see ../upgrade-code-tool.md and ../upgrade-code-scripts.md).

**Mechanical rewrites** (`_cr/_uid/_context`, `odoo.osv.expression` basics -> `odoo.fields.Domain`, `_sql_constraints`, `type="json"` -> `"jsonrpc"`, `read_group`, `toggle_active`, `@api.returns`, `@api.model_create_single`, `@ormcache_context`, `auto_join` -> `bypass_search_access`, `urljoin`) are done by `upgrade_code` and `odoo-module-migrate`, run by `port-module.sh`. The lists are in the [OCA wiki](https://github.com/OCA/maintainer-tools/wiki/Migration-to-version-19.0) and the [ORM changelog](https://www.odoo.com/documentation/19.0/developer/reference/backend/orm/changelog.html). Review the diff; grep for what the tools missed.

## TL;DR
| Change | Old (18.0) | New (19.0) |
|---|---|---|
| Group m2m fields | `groups_id` (users/views/menus/actions) | `group_ids` |
| Group app section | `res.groups.category_id` | `privilege_id` -> new `res.groups.privilege` |
| Group members | `res.groups` field `users` | `user_ids` (+ `all_user_ids`) |
| safe_eval | `safe_eval(expr, globals_dict, locals_dict, nocopy=...)` | `safe_eval(expr, context)` |
| name_search arg | `name_search(..., args=...)` | `name_search(..., domain=...)` |
| Partner phone | `res.partner.mobile` / `res.company.mobile` | removed (upstream merges into `phone`) |

## Manifest
- Bump version `18.0.x.y.z` -> `19.0.x.y.z`. No new required manifest keys (19.0 `odoo/modules/module.py` defaults list unchanged in shape; `countries`, `assets`, hooks as before).
- Python floor unchanged: `MIN_PY_VERSION = (3, 10)` in 19.0 `odoo/release.py` (18.0 setup.py already required `>=3.10`).
- Drop stale `migrations/` folders from previous series (OCA convention).

## Python / ORM
- `search=` methods of non-stored computed fields should return a `Domain` (OCA wiki, odoo/odoo@4f0d4670ed). Also, since 18.3 domain optimization runs before `search=` methods and `=` is normalized to `in` (odoo/odoo#191549) — custom `search=` implementations must handle the `in` operator with list values, not just `=`.
- `name_search()` signature is now `name_search(name='', domain=None, operator='ilike', limit=100)` — overrides/callers using `args=` must switch to `domain=`.
- `odoo.tools.safe_eval` signature changed: 18.0 `safe_eval(expr, globals_dict=None, locals_dict=None, mode="eval", nocopy=False, locals_builtins=False, filename=None)` -> 19.0 `safe_eval(expr, /, context=None, *, mode="eval", filename=None)`. `globals_dict`/`locals_dict`/`nocopy`/`locals_builtins` are gone — pass one merged `context` dict. `test_expr` was replaced by `compile_codeobj(expr, /, filename, mode)`.
- ORM package restructure (PEP-420, odoo/odoo#195664): `odoo/__init__.py` is gone (namespace package); implementation moved to `odoo/orm/*` with `odoo.fields`, `odoo.api`, `odoo.models` as thin re-export packages. Public imports (`from odoo import models, fields, api`) still work; deep imports of old file paths (e.g. patching `odoo.fields` internals) break. Import `SUPERUSER_ID` from `odoo.api` (canonical home `odoo.orm.utils`).
- `@api.private` added (18.2, odoo/odoo#195402): public (non-underscore) model methods are RPC-callable unless decorated — mark internal helpers.
- `procurement.group` removed, replaced by `stock.reference`. Rewrite `picking.group_id.mrp_production_ids` -> `picking.move_ids.reference_ids.production_ids`; `group.sale_id` -> `references.sale_ids[:1]`; `production.procurement_group_id` -> `production.reference_ids`. `_prepare_procurement_group_vals` and the pbm_sam group branch of `_run_pull` have no successor. Picking `move_type` comes from `stock.picking.type.move_type`.
- `check_version`: a manifest with a non-target series prefix is silently `installable=False`. Read `skipped[]` in the `install_all.sh` output.
- Dynamic dates in domains (odoo/odoo#216665): domain values may be literals like `'now'`, `'today'`, `'-3d'`, `'today -1m'`, `'=1d'`, `'=monday'`.

## Views / XML
- `groups_id` renamed `group_ids` on records of `res.users`, `ir.ui.view`, `ir.ui.menu`, `ir.actions.act_window`, `ir.actions.report`, `ir.actions.server` (and `website.page.properties`) — odoo/odoo#179354 + OpenUpgrade base analysis. Fix every `<field name="groups_id">` in XML and every `.groups_id` in Python. The `groups="module.group_x"` shortcut attribute on `<menuitem>`, `<field>`, view elements is unchanged.
- `res.groups` restructure (odoo/odoo#179354, commit 33637d137ed5):
  - `category_id` (ir.module.category) removed -> `privilege_id` pointing to new model `res.groups.privilege` (which itself has `category_id`, `sequence`, `placeholder`). Custom "app section" XML must create a `res.groups.privilege` record and point groups at it.
  - `users` renamed `user_ids`; computed `all_user_ids` = users incl. implied membership.
  - `implied_ids` kept; new inverse `implied_by_ids`, computed `all_implied_ids`/`all_implied_by_ids`; new `sequence`; `color` removed.
  - Name uniqueness is now `UNIQUE (privilege_id, name)` (was `unique(category_id,name)`).
  - The category xmlid `base.module_category_usability` is gone. A group without its own app section gets no privilege. Python `group.users` -> `user_ids` / `all_user_ids`.
- Search view `<group expand="0" string="Group By">` fails RELAXNG (`RELAXNG_ERR_EXTRACONTENT`, `base/rng/common.rng`). Write `<group>`.
- Report anchors moved: titles are in `<t t-set="layout_document_title">` inside `div.page`. `stock.report_picking` has `h3` + `h2 t-field="o.name"`. The sale info row is `div#informations.row.mb-4`. `l10n_us_check_printing.ckus_stub` delegates to `ckus_stub_body`. The `web.external_layout_*` logo `img` is three divs deep. Re-anchor custom xpaths.
- QWeb inherit specs need a `<data>` root (not `<t>`) for `apply_inheritance_specs` to check anything.
- `res.users`: `groups_id` -> `group_ids` (explicitly assigned groups) + computed `all_group_ids` (incl. implied). Membership checks: keep `user.has_group('module.group_x')`, or search on `all_group_ids`. New computed `role` selection (`group_user`/`group_system`).
- xmlid changes in `base`: `base.default_user` (template res.users) deleted; new `base.default_user_group` (res.groups) — XML/code referencing `base.default_user` breaks.
- `ir.filters`: `user_id` (m2o) -> `user_ids` (m2m).
- Removed core fields commonly referenced by custom views/reports: `res.partner.mobile`, `res.company.mobile` (OpenUpgrade's base migration concatenates mobile into `phone`); `res.partner.title` field and `res.partner.title` model obsolete. Views/domains/reports touching them fail to load.

## JS / Owl / Assets
Website/portal/QWeb-frontend changes are NOT enumerated here — research per-case when the module has website surface.
- Owl version unchanged: 2.8.4 on both 18.0 and 19.0 branches — no component-framework migration for this hop (blog claims of "Owl 3" are wrong).
- Asset bundle names unchanged (`web.assets_backend`, `web.assets_frontend`, ...). Only `web.qunit_mobile_suite_tests` was dropped from `web/__manifest__.py`; `web.qunit_suite_tests` still exists but new JS tests belong in Hoot (`web.assets_unit_tests`).
- The json->jsonrpc route rename is server-side only: "they are called the same, only the `type` in the python files changed" (ORM changelog 18.1) — no JS caller changes.
- Shop floor and product configurator template renames break `t-inherit` xpaths in the browser only, never at install. Grep each custom Owl xpath target.

## Hooks & misc
- Hook signatures unchanged from 18.0: `pre_init_hook(env)`, `post_init_hook(env)`, `uninstall_hook(env)` (verified in 19.0 `odoo/modules/loading.py`).
- Demo data is no longer loaded by default (18.3, odoo/odoo#194585): tests relying on demo records must create their own fixtures.
- Fake/test-only models: register natively with `odoo.orm.model_classes.add_to_registry(registry, ModelClass)` instead of the `odoo-test-helper` FakeModelLoader (OCA wiki has the full setUpClass recipe).
- Test base class: `odoo.addons.base.tests.common.BaseCommon` (sets up common env); silence chatter with `tracking_disable` in the test env context.
- Data migrations for renamed/removed **core** fields: check OpenUpgrade 19.0 per-module analysis (see Sources) before hand-writing migration scripts.

## Detection greps
The 19.0 patterns are the `19.0` rows of [detection-patterns.tsv](../detection-patterns.tsv). `scripts/port-plan.py` runs them per module and writes the hits to `plan.json`.

## Sources
- https://www.odoo.com/documentation/19.0/developer/reference/backend/orm/changelog.html
- https://github.com/OCA/maintainer-tools/wiki/Migration-to-version-19.0
- OpenUpgrade per-version analysis files at https://github.com/OCA/OpenUpgrade branch 19.0 under openupgrade_scripts/scripts/<module>/19.0.x.y.z/upgrade_analysis.txt — consult for renamed/removed core fields on edge cases (base analysis is at `base/19.0.1.3/upgrade_analysis.txt`)
- Bump `version` prefix to target series with `bump_manifest_version.py --series <target> <module dir>` (`skills/odoo-devcontainer/scripts/`). It keeps the module's own `x.y.z` tail, then applies the usual bump.
- Check `depends`: renamed/merged/removed in target? Core module renames/merges live in OpenUpgrade's `openupgrade_scripts/apriori.py` (branch = target series) — check before hand-researching.
- Check `external_dependencies` still importable on target's Python floor.
