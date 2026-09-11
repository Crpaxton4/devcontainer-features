# Breaking changes arriving in Odoo 17.0
Verified against sources 2026-07.

Port direction: 16.0 -> 17.0. No core rewrite tool this hop (odoo-bin upgrade_code start at 18.0); OCA `odoo-module-migrator` give partial 16->17 automation (incl. attrs/states patterns) — review output like any generated diff.

## TL;DR
| Change | Old (16.0) | New (17.0) |
|---|---|---|
| View modifiers | `attrs="{'invisible': [...]}"`, `states="draft"` | Python expr: `invisible="state != 'draft'"` |
| Column hiding in lists | `invisible="1"` on tree field hid column | `column_invisible="1"` hides column; `invisible` is per-cell |
| Field `states` param | `fields.Char(states={'draft': [('readonly', False)]})` | Removed (warning, ignored) — move to view `readonly=` expr |
| Record naming | override `name_get()` | override `_compute_display_name()` (deprecated since 16.4) |
| Name search | `_name_search(name, args, operator, limit, name_get_uid)` | `_name_search(name, domain, operator, limit, order)`; prefer `_rec_names_search` |
| Grouped reads (private) | `_read_group(domain, fields, groupby, ..., lazy)` -> dicts | `_read_group(domain, groupby, aggregates, having, ...)` -> tuples |
| Init hooks | `pre_init_hook(cr)`, `post_init_hook(cr, registry)` | all take single `env` arg |
| Raw SQL | `cr.execute("...%s", (p,))` string building | `odoo.tools.SQL` composable wrapper |
| Module file paths | `get_resource_path` / `get_module_resource` | `odoo.tools.misc.file_path` (old ones warn) |
| Form test helper | `from odoo.tests.common import Form` | `from odoo.tests import Form` (old path warns) |
| Owl templates | `<t t-name="..." owl="1">` | drop `owl="1"` (all templates are Owl) |
| daterange widget | paired fields, `related_start_date`/`related_end_date` options | one field, `start_date_field`/`end_date_field` options |
| Settings views | `app_settings_block` / `o_setting_box` divs | `<app>` / `<block>` / `<setting>` tags |

## Manifest
- Version prefix -> `17.0.x.y.z` (OCA convention: restart at `17.0.1.0.0`).
- Delete stale `migrations/` scripts targeting earlier versions when porting module (OCA convention).
- Hook keys (`pre_init_hook`, `post_init_hook`, `uninstall_hook`) unchanged in manifest — only Python signatures change (see Hooks & misc).

## Python / ORM
- **Field `states` parameter removed.** `fields.X(..., states={'draft': [('readonly', False)]})` logs `Since Odoo 17, property <field>.states is no longer supported.`, ignored. Re-express as view-level `readonly="state != 'draft'"` / `required=` expressions, or computed field.
- **`name_get()` deprecated** (since 16.4; still callable in 17.0, removed later — port now). Override `_compute_display_name`, read `display_name`:
  ```python
  @api.depends('name', 'code')
  def _compute_display_name(self):
      for rec in self:
          rec.display_name = f"[{rec.code}] {rec.name}"
  ```
- **`_name_search` signature changed.** 16.0: `_name_search(name='', args=None, operator='ilike', limit=100, name_get_uid=None)` returning ids. 17.0: `_name_search(name, domain=None, operator='ilike', limit=None, order=None)` returning a query. Overrides must be rewritten; for "also match on field X" cases drop the override entirely:
  ```python
  _rec_names_search = ['name', 'code']
  ```
- **`_read_group` (private) new signature** (since 16.3): `_read_group(domain, groupby=(), aggregates=(), having=(), offset=0, limit=None, order=None)` returning a list of tuples. Overrides and direct callers must be rewritten. Public `read_group(domain, fields, groupby, ...)` keeps the old dict-based API in 17.0.
- **`odoo.tools.SQL` wrapper** (odoo/odoo PR #134677): injection-safe, composable raw SQL; `cr.execute()` accepts `SQL` objects directly.
  ```python
  from odoo.tools import SQL
  cr.execute(SQL("UPDATE my_table SET x_field = %s WHERE id = %s", val, rec_id))
  sub = SQL("COALESCE(%s, %s)", SQL.identifier('x_field'), 0)   # composes safely
  cr.execute(SQL("SELECT %s FROM my_table", sub))
  ```
  Not mandatory for custom code in 17.0, but new core helpers (e.g. `_read_group_*`) return `SQL`, so overrides of those must use it.
- **`check_company=True` domains** now come from the overridable model method `_check_company_domain(companies)` (default: `['|', ('company_id', '=', False), ('company_id', 'in', company_ids)]`). Models with a differently-named company field or custom consistency rules override this method instead of hardcoding domains.
- **`get_resource_path` / `get_module_resource` deprecated** — warning `Since 17.0: use tools.misc.file_path instead`. New call takes one relative path: `file_path('my_module/data/file.csv')`.
- **New perf APIs available** (since 16.2): `search_fetch(domain, field_names, ...)` and `fetch(field_names)` combine search+read in fewer queries; `search_count(domain, limit=N)` honors `limit`. Optional adoption.
- **Tests:**
  - `odoo.tests.common.Form` deprecated -> `from odoo.tests import Form`.
  - Outbound HTTP is blocked in test mode — tests hitting external services must mock (`unittest.mock.patch('requests.get')`, etc.).

## Views / XML
- **`attrs` and `states` attributes no longer parse — hard error on install.** This is the dominant 16->17 work item. Each `attrs` key becomes its own attribute holding a Python expression; `states="a,b"` becomes an `invisible` expression.

  Conversion rules (domain -> expression):
  | 16.0 | 17.0 |
  |---|---|
  | `attrs="{'invisible': [('state', '!=', 'draft')]}"` | `invisible="state != 'draft'"` |
  | `attrs="{'readonly': [('a', '=', 1), ('b', '=', 2)]}"` (implicit AND) | `readonly="a == 1 and b == 2"` |
  | `attrs="{'required': ['|', ('a', '=', 1), ('b', '=', 2)]}"` | `required="a == 1 or b == 2"` |
  | `[('state', 'in', ['a', 'b'])]` / `not in` | `state in ['a', 'b']` / `state not in [...]` |
  | `[('x_field', '=', False)]` / `[('x_field', '!=', False)]` | `not x_field` / `x_field` |
  | `[('parent.state', '=', 'done')]` (in sub-views) | `parent.state == 'done'` |
  | `states="draft,sent"` (field/button) | `invisible="state not in ['draft', 'sent']"` |
- Expression namespace: field names present in the current view, `parent` (sub-views of relational fields only), `context`, `uid`, `today` (`YYYY-MM-DD` str), `now` (`YYYY-MM-DD hh:mm:ss` str). E.g. `invisible="context.get('hide_x') or state == 'done'"`. A field referenced in an expression must be present in the view — add it `invisible="1"` if needed.
- **`column_invisible` vs `invisible` in list (sub)views:** `invisible` now evaluates per record and blanks the cell; `column_invisible` removes the whole column. Old tree-view `invisible="1"` column-hiding must become `column_invisible="1"`. `column_invisible` expressions cannot reference the row's field values (no record at evaluation time) — constants and `parent.*` only, e.g. `column_invisible="parent.state != 'draft'"`.
- **Inherited views:** `<attribute name="attrs">` / `<attribute name="states">` overrides fail like any other use; replace with the target attribute directly: `<attribute name="invisible">state != 'draft'</attribute>`.
- **Settings views restructured:** `<div class="app_settings_block">` -> `<app>`, `<div class="o_settings_container">` + `<h2>` -> `<block title="...">`, `<div class="o_setting_box">` -> `<setting>`; drop `o_setting_left_pane`/`o_setting_right_pane` inner divs.
- **Deprecated context vars in view attributes:** replace `active_id` with `id` and `active_model` with the hard-coded model name in view `context=`/`domain=` (deprecation warnings otherwise).
- Model-level `readonly=True` fields are not importable via the import UI in 17.0 — if import must work, make the field readonly in the view instead.
- **Not yet in 17.0** (do not convert on this hop): `<tree>` stays `<tree>` (the `<list>` rename is 18.0); kanban `t-name="kanban-box"` templates are unchanged (card rewrite is 18.0); chatter `<div class="oe_chatter">` markup is unchanged.

## JS / Owl / Assets
Website/portal/QWeb-frontend changes are NOT enumerated here — research per-case when the module has website surface.
- Remove `owl="1"` from all JS QWeb templates — every template is an Owl template now (odoo/odoo PR #130467); the attribute is obsolete.
- `/** @odoo-module **/` header is still required in 17.0 for native-module JS files (opt-in system unchanged) — do not strip it.
- **daterange widget rewritten** (now part of the datetime field family): 16.0 put `widget="daterange"` on both fields with `options="{'related_start_date': ...}"` / `{'related_end_date': ...}`; 17.0 uses a single field carrying the widget with `options="{'end_date_field': 'x_date_end'}"` (or `start_date_field`), plus new `always_range` option. The `related_*` options are silently dead.
- Asset bundle declaration in `__manifest__.py` `'assets'` key: unchanged from 16.0.

## Hooks & misc
- **Init hook signatures — all take a single `env`:**
  ```python
  def pre_init_hook(env): ...        # was (cr)
  def post_init_hook(env): ...       # was (cr, registry)
  def uninstall_hook(env): ...       # was (cr, registry)
  ```
  Use `env.cr` where the old body used `cr`; drop manual `api.Environment(...)` construction.
- Python >= 3.10 required (`python_requires='>=3.10'` in odoo/odoo 17.0 setup.py) — check f-string/typing usage in vendored helpers and CI images.
- Renamed/removed **core model fields** (base, sale, stock, account, mrp, ...) are module-specific — check every core field your modules reference (see Sources for OpenUpgrade analysis files).

## Detection greps
```bash
# Views: attrs/states (the big one) + inheritance overrides of them
grep -rn 'attrs=' --include='*.xml' .
grep -rnE 'states="[^"]*"' --include='*.xml' .
grep -rn '<attribute name="\(attrs\|states\)"' --include='*.xml' .
# Tree-view column hiding to re-check per column_invisible semantics
grep -rn 'column_invisible\|<tree' --include='*.xml' . | grep 'invisible='
# Python field states param
grep -rn 'states={' --include='*.py' .
# ORM overrides needing new signatures / replacements
grep -rn 'def name_get\|def _name_search\|_read_group(' --include='*.py' .
# Hooks
grep -rnE 'def (pre_init_hook|post_init_hook|uninstall_hook)' --include='*.py' .
# Deprecated helpers and imports
grep -rn 'get_resource_path\|get_module_resource' --include='*.py' .
grep -rn 'tests.common import.*Form\|tests\.common\.Form' --include='*.py' .
# Raw SQL: candidates for odoo.tools.SQL
grep -rn '\.execute(' --include='*.py' .
# JS/templates
grep -rn 'owl="1"' --include='*.xml' .
grep -rn 'related_start_date\|related_end_date' --include='*.xml' .
# Settings views
grep -rn 'app_settings_block\|o_settings_container\|o_setting_box' --include='*.xml' .
# Deprecated context vars in view attributes (review hits manually)
grep -rn 'active_id\|active_model' --include='*.xml' .
```

## Sources
- https://github.com/OCA/maintainer-tools/wiki/Migration-to-version-17.0
- https://www.odoo.com/documentation/17.0/developer/reference/backend/orm/changelog.html
- OpenUpgrade per-version analysis files live at https://github.com/OCA/OpenUpgrade branch 17.0 under `openupgrade_scripts/scripts/<module>/17.0.x.y.z/upgrade_analysis.txt` — consult for renamed/removed core model fields on edge cases