# Breaking changes arriving in Odoo 18.0
Verified against sources 2026-07.

Port direction: 17.0 -> 18.0. Automated tool exists: run Odoo 18 `odoo-bin upgrade_code --from 17.0 --to 18.0` first (see ../upgrade-code-tool.md and ./upgrade-code-scripts.md), then work list manually.

## TL;DR
| Change | Old (17.0) | New (18.0) |
|---|---|---|
| List views | `<tree>`, `view_mode="tree,form"` | `<list>`, `view_mode="list,form"` |
| Group check | `self.user_has_groups("a.b")` | `self.env.user.has_group("a.b")` |
| Aggregation attr | `group_operator="sum"` | `aggregator="sum"` |
| Chatter | `<div class="oe_chatter">` + 3 fields | `<chatter/>` |
| Access API | `check_access_rights` / `check_access_rule` / `_filter_access_rules` | `check_access` / `has_access` / `_filtered_access` |
| Name search override | `_name_search` | `_search_display_name` |
| Record naming | `name_get()` | `display_name` / `_compute_display_name` |
| Recursion check | `_check_recursion()` | `not _has_cycle()` |
| Kanban template | `t-name="kanban-box"` | `t-name="card"` |
| Translation | `from odoo import _` | `self.env._(...)` preferred |
| copy_data return | single dict | list of dicts (multi-record) |
| Registry import | `from odoo import registry` | `from odoo.modules.registry import Registry` |

## Manifest
- Bump version `17.0.x.y.z` -> `18.0.x.y.z`. No new required manifest keys.
- Drop stale `migrations/` folders from previous series (OCA convention).

## Python / ORM
- `user_has_groups()` **removed** (odoo/odoo#151597). Use `self.env.user.has_group("module.group_xml_id")`. Takes exactly one group: split old comma-separated / `!`-negated specs into multiple `has_group()` calls joined with `and`/`not`.
- New unified access API (odoo/odoo#179148), combines ACLs + record rules:
  - `record.check_access("read"|"write"|"create"|"unlink")` — raises `AccessError`.
  - `record.has_access(operation)` — returns bool (replaces `check_access_rights(..., raise_exception=False)`).
  - `record._filtered_access(operation)` — returns allowed subset (replaces `_filter_access_rules` / `_filter_access_rules_python`).
  - `check_access_rights()`, `check_access_rule()`, `_filter_access_rules*()` still exist as `DeprecationWarning` shims in 18.0 `models.py` — port now.
- Field attribute `group_operator` renamed -> `aggregator` (odoo/odoo#127353).
- `_search_display_name(operator, value)` (odoo/odoo#174967): search on `display_name` goes through this method like any field. Override it (return domain) instead of `_name_search`; default honors `_rec_names_search` / `_rec_name`.
- `name_get()` **removed** in 18.0 (deprecated in 17.0). Read `display_name` / `records.mapped("display_name")`; customize via `_compute_display_name` (assign `rec.display_name` per record — worked example in ../17.0/changes.md).
- `_check_recursion()` / `_check_m2m_recursion()` deprecated -> `not self._has_cycle(field_name)`. **Polarity inverted**: old methods returned True when no loop; `_has_cycle` returns True when cycle exists.
- Translations from environment (odoo/odoo#174844): prefer `self.env._("Message")` inside model methods; module-level `from odoo import _` still works.
- `copy()` and `copy_data()` now work on multi-recordsets; `copy_data()` returns **list of dicts** (one per record). Fix overrides with `return super().copy_data(default)[0]`-style assumptions.
- `from odoo import registry` deprecated -> `from odoo.modules.registry import Registry`; `Registry(db_name)`.
- Search on non-stored related field without search method now **raises** (was logged warning).
- Post-process search results: override `search_fetch()`, not `search()`.
- Domain operator `inselect` removed (odoo/odoo#171371) -> use `in` with `Query` or `SQL` object.
- `_flush_search()` deprecated (odoo/odoo#144747); flushing derived from SQL metadata in `execute_query()`.

## Views / XML
- `<tree>` -> `<list>` everywhere. Covers: arch root tag `<tree>`/`</tree>` (attributes like `editable="bottom"` unchanged), xpath `expr="//tree"` / `expr="//tree/field[...]"`, `view_mode`/`binding_view_types` field values, `mode="tree"` on inherited views, context key `tree_view_ref` -> `list_view_ref`, `"views"` maps in Python/JS. Core script `17.5-01-tree-to-list.py` does most mechanically (see ./upgrade-code-scripts.md). Do **not** rename XML record ids containing `tree` — dependent modules may reference them.
- Chatter: replace whole block
  `<div class="oe_chatter"><field name="message_follower_ids"/><field name="activity_ids"/><field name="message_ids"/></div>`
  with single `<chatter/>` tag (optional attrs exist, e.g. `<chatter reload_on_post="True"/>` as in core views).
- Kanban views restructured: `<t t-name="kanban-box">` -> `<t t-name="card">`; drop wrapper `<div>` layers, use plain `<field>` elements with `class` attributes; `kanban-tooltip` template removed; `<ul class="oe_kanban_colorpicker" data-field="color"/>` -> `<field name="color" widget="kanban_color_picker"/>`; `<t t-name="menu">` still exists.
- Fields used only in `invisible` / `column_invisible` / `readonly` / `required` / `domain` / `context` expressions now auto-injected as invisible fields — explicit `<field name="x_field" invisible="1"/>` stubs deletable.
- `ir.actions.act_window` gains optional `path` char field ("Path to show in the URL", unique) for readable action URLs — set on main menu actions.

## JS / Owl / Assets
Website/portal/QWeb-frontend changes NOT enumerated here — research per-case when module has website surface.
- `/** @odoo-module **/` header no longer needed — remove from JS files.
- Test tours: `extra_trigger` removed — split into own step using that trigger; remove `test: true,` from tour definitions.
- Tree->list rename also applies in JS: `view_mode` strings, `views` descriptors, `"Tree view"` labels (core rewrite script handles).

## Hooks & misc
- Python >= 3.10 required (`python_requires='>=3.10'` in odoo/odoo 18.0 setup.py).
- Tests: silence chatter noise with `cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))` (or OCA `BaseCommon`).
- Data migrations for renamed/removed **core** fields: check OpenUpgrade 18.0 per-module analysis (see Sources) before hand-writing migration scripts.

## Detection greps
```bash
grep -rn "<tree\|</tree>\|//tree" --include="*.xml" .
grep -rn "view_mode.*tree\|tree_view_ref\|mode=\"tree\"" .
grep -rn "oe_chatter" --include="*.xml" .
grep -rn "kanban-box\|kanban-tooltip\|oe_kanban_colorpicker" --include="*.xml" .
grep -rn "user_has_groups\|name_get(\|_name_search\|_check_recursion\|_check_m2m_recursion" --include="*.py" .
grep -rn "check_access_rights\|check_access_rule\|_filter_access_rules" --include="*.py" .
grep -rn "group_operator" --include="*.py" .
grep -rn "from odoo import registry\|inselect\|_flush_search" --include="*.py" .
grep -rn "@odoo-module\|extra_trigger\|test: true" --include="*.js" .
```

## Sources
- https://github.com/OCA/maintainer-tools/wiki/Migration-to-version-18.0
- https://www.odoo.com/documentation/18.0/developer/reference/backend/orm/changelog.html
- OpenUpgrade per-version analysis files at https://github.com/OCA/OpenUpgrade branch 18.0 under openupgrade_scripts/scripts/<module>/18.0.x.y.z/upgrade_analysis.txt — consult for renamed/removed core fields on edge cases