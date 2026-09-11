# odoo-bin upgrade_code scripts shipped in Odoo 18.0
Branch: odoo/odoo@18.0, dir odoo/upgrade_code/. Run via Odoo 18 odoo-bin (see ../upgrade-code-tool.md).

| Script | Rewrites | Notes/limits |
|---|---|---|
| `17.5-00-example.py` | Nothing (FileManager API demo) | Write-back line commented out; docstring say "Don't use this script in production". Skip. |
| `17.5-01-tree-to-list.py` | tree -> list across `.xml`, `.js`, `.py`: arch tags `<tree>`/`</tree>`; xpath `expr="...tree..."`; `view_mode`/`name`/`binding_view_types` field values; `mode="tree"`; `tree_view_ref` -> `list_view_ref`; `view_mode`/`views` strings in Python/JS dicts; literal "tree view"/"Tree view" text; `self.env.ref("...tree")` strings | Pure regex, no XML parsing — review diff. NOT rename XML record `id=` attributes, yet DOES rewrite `env.ref()` strings ending in `tree` — can create dangling refs; revert those hunks (keep old XML ids). Miss `tree` in translated terms, docstrings, comments. |

Invocation (against your addons dir):
```bash
odoo-bin upgrade_code --from 17.0 --to 18.0 --addons-path /path/to/addons
# or a single script:
odoo-bin upgrade_code --script 17.5-01-tree-to-list.py --addons-path /path/to/addons
```

Everything else in [./changes.md](./changes.md) manual.