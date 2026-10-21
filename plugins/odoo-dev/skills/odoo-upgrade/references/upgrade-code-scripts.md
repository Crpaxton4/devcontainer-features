# odoo-bin upgrade_code scripts, by the series that ships them
Branch: odoo/odoo, dir odoo/upgrade_code/. "Ships in" is the first Odoo series whose odoo-bin carries the script. Run via the target series odoo-bin.

CLI flags, `--dry-run`, the exit code (`1` when files changed) and the touched file extensions: see [upgrade-code-tool.md](./upgrade-code-tool.md).

| Script | Ships in | Rewrites | Notes/limits |
|---|---|---|---|
| `17.5-00-example.py` | 18.0 | Nothing (FileManager API demo) | Write-back line commented out; docstring say "Don't use this script in production". Skip. |
| `17.5-01-tree-to-list.py` | 18.0 | tree -> list across `.xml`, `.js`, `.py`: arch tags `<tree>`/`</tree>`; xpath `expr="...tree..."`; `view_mode`/`name`/`binding_view_types` field values; `mode="tree"`; `tree_view_ref` -> `list_view_ref`; `view_mode`/`views` strings in Python/JS dicts; literal "tree view"/"Tree view" text; `self.env.ref("...tree")` strings | 17.0->18.0 hop only. Pure regex, no XML parsing — review diff. NOT rename XML record `id=` attributes, yet DOES rewrite `env.ref()` strings ending in `tree` — can create dangling refs; revert those hunks (keep old XML ids). Miss `tree` in translated terms, docstrings, comments. |
| `18.1-00-sql-constraint.py` | 19.0 | `_sql_constraints = [(name, def, msg)]` -> one `_<name> = models.Constraint(def, msg)` class attribute per tuple, all `.py` | Regex + `ast.literal_eval`: only literal lists convert; dynamic/computed lists skipped, logged ("Failed to replace in file ..."). No `models.Index` added. |
| `18.1-02-route-jsonrpc.py` | 19.0 | `type="json",` / `type='json',` -> `type="jsonrpc",` | Only `.py` inside `controllers/` dir, only with trailing comma. Routes elsewhere (models, wizards) or other format: manual edit. |
| `18.2-00-l10n-translate.py` | 19.0 | Localization data translation restructure: inline translations from `l10n_*/i18n/*.po|.pot` into data `.xml`/`.csv` under `data/`, hard-coded model list (`account.*`, `hr.*`, `l10n_*`, ...) | l10n template modules only; irrelevant for typical custom addons. |
| `18.3-00-l10n-fiscal-position-taxes.py` | 19.0 | l10n chart-template fiscal-position/tax mapping data, hard-coded per-country lookup table | l10n template modules only. |
| `18.5-00-deprecated-properties.py` | 19.0 | `._cr` / `._uid` / `._context` -> `.env.cr` / `.env.uid` / `.env.context`, all `.py` | Blind regex `\._(cr|uid|context)\b`: also hit non-recordset objects with same attribute names — review diff. |
| `18.5-00-domain-dynamic-dates.py` | 19.0 | XML domains (`<filter domain=...>`, `<field name="domain">`, record-rule `domain_force`): `context_today()` / `datetime.datetime.now()` / `relativedelta(...)` / `.strftime(...)` expressions -> new dynamic-date literals (`'now'`, `'today'`, `'-3d'`, `'today -1m'`, `'=1d'`, `'=monday'`) | Only `.xml` with direct parent dir `data/`, `report/` or `views/`. AST-parse each domain; unconvertible ones logged, left alone. Python-built domains untouched. |
| `18.5-00-no-tax-tag-invert.py` | 19.0 | l10n tax data (`repartition_line_ids/*` CSV columns, report formulas): flip `+`/`-` tag signs for removal of automatic tax-tag inversion | l10n template modules only. |

Hop rule: `--from 18.0` excludes the 17.5 scripts (17.5 < 18.0). A 17.0->19.0 run with the 19 odoo-bin applies all nine in one pass.

Invocation (against your addons dir):
```bash
odoo-bin upgrade_code --from 17.0 --to 18.0 --addons-path /path/to/addons
odoo-bin upgrade_code --from 18.0 --to 19.0 --addons-path /path/to/addons
# or a single script:
odoo-bin upgrade_code --script 17.5-01-tree-to-list.py --addons-path /path/to/addons
odoo-bin upgrade_code --script 18.5-00-deprecated-properties --addons-path /path/to/addons
```

Everything else is manual: see `18.0/changes.md` and `19.0/changes.md`.
