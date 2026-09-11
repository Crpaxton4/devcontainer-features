# odoo-bin upgrade_code scripts shipped in Odoo 19.0
Branch: odoo/odoo@19.0, dir odoo/upgrade_code/. Run via Odoo 19 odoo-bin (see ../upgrade-code-tool.md). All 18.0-era scripts included — 17.0->19.0 run applies all below in one pass.

| Script | Rewrites | Notes/limits |
|---|---|---|
| `17.5-00-example.py` | Nothing (FileManager API demo) | Write-back line commented out; docstring say not for production. Skip. |
| `17.5-01-tree-to-list.py` | tree -> list across `.xml`/`.js`/`.py`: `<tree>` tags, xpath `expr`, `view_mode`/`views`/`mode` values, `tree_view_ref` -> `list_view_ref`, "tree view" strings, `env.ref("...tree")` strings | 17.0->18.0 hop only; `--from 18.0` excludes it (17.5 < 18.0). Pure regex; can turn `env.ref()` strings into dangling xmlids — review. |
| `18.1-00-sql-constraint.py` | `_sql_constraints = [(name, def, msg)]` -> one `_<name> = models.Constraint(def, msg)` class attribute per tuple, all `.py` | Regex + `ast.literal_eval`: only literal lists convert; dynamic/computed lists skipped, logged ("Failed to replace in file ..."). No `models.Index` added. |
| `18.1-02-route-jsonrpc.py` | `type="json",` / `type='json',` -> `type="jsonrpc",` | Only `.py` inside `controllers/` dir, only with trailing comma. Routes elsewhere (models, wizards) or other format: manual edit. |
| `18.2-00-l10n-translate.py` | Localization data translation restructure: inline translations from `l10n_*/i18n/*.po|.pot` into data `.xml`/`.csv` under `data/`, hard-coded model list (`account.*`, `hr.*`, `l10n_*`, ...) | l10n template modules only; irrelevant for typical custom addons. |
| `18.3-00-l10n-fiscal-position-taxes.py` | l10n chart-template fiscal-position/tax mapping data, hard-coded per-country lookup table | l10n template modules only. |
| `18.5-00-deprecated-properties.py` | `._cr` / `._uid` / `._context` -> `.env.cr` / `.env.uid` / `.env.context`, all `.py` | Blind regex `\._(cr|uid|context)\b`: also hit non-recordset objects with same attribute names — review diff. |
| `18.5-00-domain-dynamic-dates.py` | XML domains (`<filter domain=...>`, `<field name="domain">`, record-rule `domain_force`): `context_today()` / `datetime.datetime.now()` / `relativedelta(...)` / `.strftime(...)` expressions -> new dynamic-date literals (`'now'`, `'today'`, `'-3d'`, `'today -1m'`, `'=1d'`, `'=monday'`) | Only `.xml` with direct parent dir `data/`, `report/` or `views/`. AST-parse each domain; unconvertible ones logged, left alone. Python-built domains untouched. |
| `18.5-00-no-tax-tag-invert.py` | l10n tax data (`repartition_line_ids/*` CSV columns, report formulas): flip `+`/`-` tag signs for removal of automatic tax-tag inversion | l10n template modules only. |

Invocation (against your addons dir):
```bash
odoo-bin upgrade_code --from 18.0 --to 19.0 --addons-path /path/to/addons
# or a single script:
odoo-bin upgrade_code --script 18.5-00-deprecated-properties --addons-path /path/to/addons
```

Everything else in [./changes.md](./changes.md): manual.