# Breaking changes arriving in Odoo 19.0
Verified against sources 2026-07.

Port direction: 18.0 -> 19.0. Automated tool exists: run Odoo 19 `odoo-bin upgrade_code --from 18.0 --to 19.0` first (see ../upgrade-code-tool.md and ./upgrade-code-scripts.md), then work list manually.

## TL;DR
| Change | Old (18.0) | New (19.0) |
|---|---|---|
| Cursor/context/uid | `record._cr` / `._context` / `._uid` | `record.env.cr` / `.env.context` / `.env.uid` |
| Domain API | `odoo.osv.expression` (`AND`/`OR`/...) | `odoo.fields.Domain` |
| JSON routes | `@route(type="json")` | `@route(type="jsonrpc")` |
| SQL constraints | `_sql_constraints = [(name, def, msg)]` | `_name = models.Constraint(def, msg)` attrs |
| Group m2m fields | `groups_id` (users/views/menus/actions) | `group_ids` |
| Group app section | `res.groups.category_id` | `privilege_id` -> new `res.groups.privilege` |
| Group members | `res.groups` field `users` | `user_ids` (+ `all_user_ids`) |
| safe_eval | `safe_eval(expr, globals_dict, locals_dict, nocopy=...)` | `safe_eval(expr, context)` |
| auto_join | `fields.Many2one(..., auto_join=True)` | `bypass_search_access=True` |
| read_group | `read_group()` (deprecated 18.2) | `_read_group()` / `formatted_read_group()` |
| Archive toggle | `toggle_active()` (deprecated) | `action_archive()` / `action_unarchive()` |
| Create decorator | `@api.model_create_single` (removed) | `@api.model_create_multi` |
| Returns decorator | `@api.returns` (removed) | drop; adapt callers |
| Cache decorator | `@ormcache_context` (deprecated) | `@ormcache` + `self.env.context.get()` |
| name_search arg | `name_search(..., args=...)` | `name_search(..., domain=...)` |
| Partner phone | `res.partner.mobile` / `res.company.mobile` | removed (upstream merges into `phone`) |

## Manifest
- Bump version `18.0.x.y.z` -> `19.0.x.y.z`. No new required manifest keys (19.0 `odoo/modules/module.py` defaults list unchanged in shape; `countries`, `assets`, hooks as before).
- Python floor unchanged: `MIN_PY_VERSION = (3, 10)` in 19.0 `odoo/release.py` (18.0 setup.py already required `>=3.10`).
- Drop stale `migrations/` folders from previous series (OCA convention).

## Python / ORM
- `record._cr`, `record._context`, `record._uid` deprecated (odoo/odoo#193636). Use `record.env.cr`, `record.env.context`, `record.env.uid`. Core script `18.5-00-deprecated-properties` rewrites them.
- `odoo.osv` deprecated (odoo/odoo#217708). Every `odoo.osv.expression` helper (`AND`, `OR`, `distribute_not`, `normalize_domain`, `prettify_domain`, `expression()` class, ...) still works, emits `DeprecationWarning: Since 19.0, use odoo.fields.Domain`. New API (`odoo.fields.Domain`, implemented in `odoo.orm.domains`):
  ```python
  from odoo.fields import Domain
  d = Domain('state', '=', 'done') & Domain('partner_id', '!=', False)   # & | ~ operators
  d = Domain.AND([d, [('active', '=', True)]])                           # lists auto-convert
  d = Domain.OR([...]); Domain.TRUE; Domain.FALSE
  d.is_true(); d.is_false(); d.iter_conditions(); d.map_conditions(fn); d.optimize(model)
  ```
  `Domain.custom(...)` injects arbitrary SQL conditions (odoo/odoo#205208). `search()` and friends accept both `Domain` and plain lists.
- `search=` methods of non-stored computed fields should return a `Domain` (OCA wiki, odoo/odoo@4f0d4670ed). Also, since 18.3 domain optimization runs before `search=` methods and `=` is normalized to `in` (odoo/odoo#191549) — custom `search=` implementations must handle the `in` operator with list values, not just `=`.
- `_sql_constraints` list-of-tuples -> class attributes (odoo/odoo#175783). One attribute per constraint; leading `_` is stripped to form the constraint name. `models.Index` / `models.UniqueIndex` exist too (all from `odoo.orm.table_objects`):
  ```python
  _code_uniq = models.Constraint('UNIQUE (code)', "The code must be unique.")
  _positive_qty = models.Constraint('CHECK (qty >= 0)')
  _partner_idx = models.Index('(partner_id) WHERE active')
  ```
  Core script `18.1-00-sql-constraint` converts literal lists.
- `read_group()` deprecated since 18.2 (odoo/odoo#163300): use `_read_group()` in backend code, `formatted_read_group()` for the formatted public/RPC API.
- `toggle_active()` deprecated (`@api.deprecated`, odoo/odoo#183691): call `action_archive()` / `action_unarchive()` explicitly.
- `name_search()` signature is now `name_search(name='', domain=None, operator='ilike', limit=100)` — overrides/callers using `args=` must switch to `domain=`.
- `@api.returns` removed (odoo/odoo#182709) — `odoo.api` no longer exports it; drop the decorator and adapt callers that relied on the id/record conversion.
- `@api.model_create_single` removed (still present in 18.0 `odoo/api.py`, gone from 19.0 `odoo/orm/decorators.py`): rewrite `create()` overrides to `@api.model_create_multi` taking `vals_list`.
- `@ormcache_context` deprecated (odoo/odoo#220725): "use ormcache directly, context values are available as `self.env.context.get`".
- `auto_join` field parameter renamed `bypass_search_access` (odoo/odoo#219627). No compat alias in 19.0 field classes — hard break.
- `odoo.tools.safe_eval` signature changed: 18.0 `safe_eval(expr, globals_dict=None, locals_dict=None, mode="eval", nocopy=False, locals_builtins=False, filename=None)` -> 19.0 `safe_eval(expr, /, context=None, *, mode="eval", filename=None)`. `globals_dict`/`locals_dict`/`nocopy`/`locals_builtins` are gone — pass one merged `context` dict. `test_expr` was replaced by `compile_codeobj(expr, /, filename, mode)`.
- ORM package restructure (PEP-420, odoo/odoo#195664): `odoo/__init__.py` is gone (namespace package); implementation moved to `odoo/orm/*` with `odoo.fields`, `odoo.api`, `odoo.models` as thin re-export packages. Public imports (`from odoo import models, fields, api`) still work; deep imports of old file paths (e.g. patching `odoo.fields` internals) break. Import `SUPERUSER_ID` from `odoo.api` (canonical home `odoo.orm.utils`).
- New `self.env.tz` timezone property (odoo/odoo#221541): replaces manual `pytz.timezone(self.env.context.get('tz')...)` handling.
- `@api.private` added (18.2, odoo/odoo#195402): public (non-underscore) model methods are RPC-callable unless decorated — mark internal helpers.
- `odoo.tools.urls.urljoin` helper added — use instead of `urllib.parse.urljoin` for Odoo URLs (OCA wiki, odoo/odoo@977e62d91f3e).
- Dynamic dates in domains (odoo/odoo#216665): domain values may be literals like `'now'`, `'today'`, `'-3d'`, `'today -1m'`, `'=1d'`, `'=monday'`.

## Views / XML
- `groups_id` renamed `group_ids` on records of `res.users`, `ir.ui.view`, `ir.ui.menu`, `ir.actions.act_window`, `ir.actions.report`, `ir.actions.server` (and `website.page.properties`) — odoo/odoo#179354 + OpenUpgrade base analysis. Fix every `<field name="groups_id">` in XML and every `.groups_id` in Python. The `groups="module.group_x"` shortcut attribute on `<menuitem>`, `<field>`, view elements is unchanged.
- `res.groups` restructure (odoo/odoo#179354, commit 33637d137ed5):
  - `category_id` (ir.module.category) removed -> `privilege_id` pointing to new model `res.groups.privilege` (which itself has `category_id`, `sequence`, `placeholder`). Custom "app section" XML must create a `res.groups.privilege` record and point groups at it.
  - `users` renamed `user_ids`; computed `all_user_ids` = users incl. implied membership.
  - `implied_ids` kept; new inverse `implied_by_ids`, computed `all_implied_ids`/`all_implied_by_ids`; new `sequence`; `color` removed.
  - Name uniqueness is now `UNIQUE (privilege_id, name)` (was `unique(category_id,name)`).
- `res.users`: `groups_id` -> `group_ids` (explicitly assigned groups) + computed `all_group_ids` (incl. implied). Membership checks: keep `user.has_group('module.group_x')`, or search on `all_group_ids`. New computed `role` selection (`group_user`/`group_system`).
- xmlid changes in `base`: `base.default_user` (template res.users) deleted; new `base.default_user_group` (res.groups) — XML/code referencing `base.default_user` breaks.
- `ir.filters`: `user_id` (m2o) -> `user_ids` (m2m).
- Removed core fields commonly referenced by custom views/reports: `res.partner.mobile`, `res.company.mobile` (OpenUpgrade's base migration concatenates mobile into `phone`); `res.partner.title` field and `res.partner.title` model obsolete. Views/domains/reports touching them fail to load.
- XML domains with `context_today()` / `relativedelta(...)` can be rewritten to the new dynamic-date literals; core script `18.5-00-domain-dynamic-dates` converts `<filter domain>`, `<field name="domain">` and record-rule `domain_force` under `data/`, `report/`, `views/`.

## JS / Owl / Assets
Website/portal/QWeb-frontend changes are NOT enumerated here — research per-case when the module has website surface.
- Owl version unchanged: 2.8.4 on both 18.0 and 19.0 branches — no component-framework migration for this hop (blog claims of "Owl 3" are wrong).
- Asset bundle names unchanged (`web.assets_backend`, `web.assets_frontend`, ...). Only `web.qunit_mobile_suite_tests` was dropped from `web/__manifest__.py`; `web.qunit_suite_tests` still exists but new JS tests belong in Hoot (`web.assets_unit_tests`).
- The json->jsonrpc route rename is server-side only: "they are called the same, only the `type` in the python files changed" (ORM changelog 18.1) — no JS caller changes.

## Hooks & misc
- Hook signatures unchanged from 18.0: `pre_init_hook(env)`, `post_init_hook(env)`, `uninstall_hook(env)` (verified in 19.0 `odoo/modules/loading.py`).
- Controllers: `@route(type="json")` -> `type="jsonrpc")` (odoo/odoo#183636). Core script `18.1-02-route-jsonrpc` rewrites only `controllers/*.py` with a trailing comma — grep for the rest.
- Demo data is no longer loaded by default (18.3, odoo/odoo#194585): tests relying on demo records must create their own fixtures.
- Fake/test-only models: register natively with `odoo.orm.model_classes.add_to_registry(registry, ModelClass)` instead of the `odoo-test-helper` FakeModelLoader (OCA wiki has the full setUpClass recipe).
- Test base class: `odoo.addons.base.tests.common.BaseCommon` (sets up common env); silence chatter with `tracking_disable` in the test env context.
- New CLI `reinit` option to reinitialize modules (18.4, odoo/odoo#206408).
- Data migrations for renamed/removed **core** fields: check OpenUpgrade 19.0 per-module analysis (see Sources) before hand-writing migration scripts.

## Detection greps
```bash
grep -rn "\._cr\b\|\._uid\b\|\._context\b" --include="*.py" .
grep -rn "osv.expression\|from odoo.osv\|from odoo import SUPERUSER_ID" --include="*.py" .
grep -rn "type=[\"']json[\"']" --include="*.py" .
grep -rn "_sql_constraints" --include="*.py" .
grep -rn "auto_join\|model_create_single\|api\.returns\|ormcache_context\|toggle_active" --include="*.py" .
grep -rn "\.read_group(\|name_search(.*args=" --include="*.py" .
grep -rn "globals_dict\|locals_dict\|nocopy" --include="*.py" .
grep -rn "groups_id" .
grep -rn "name=\"users\"\|name=\"category_id\"" --include="*.xml" .   # res.groups records
grep -rn "base\.default_user\b" . | grep -v default_user_group
grep -rn "name=\"mobile\"\|'mobile'\|\"mobile\"" .                    # removed partner/company field
grep -rn "res\.partner\.title\|name=\"title\"" --include="*.xml" .
grep -rn "context_today\|relativedelta" --include="*.xml" .           # rewritable to dynamic dates
```

## Sources
- https://www.odoo.com/documentation/19.0/developer/reference/backend/orm/changelog.html
- https://github.com/OCA/maintainer-tools/wiki/Migration-to-version-19.0
- OpenUpgrade per-version analysis files at https://github.com/OCA/OpenUpgrade branch 19.0 under openupgrade_scripts/scripts/<module>/19.0.x.y.z/upgrade_analysis.txt — consult for renamed/removed core fields on edge cases (base analysis is at `base/19.0.1.3/upgrade_analysis.txt`)