# Manifest Keys

`__manifest__.py` — module metadata dict. All keys are optional except `name`.

## Required

```python
{
    'name': 'My Addon',  # Display name — REQUIRED
}
```

## Common Keys

```python
{
    'name': 'My Addon',
    'version': '19.0.1.0.0',           # {odoo}.{major}.{minor}.{patch}
    'summary': 'One-line description',
    'description': """Long description (RST or plain text)""",
    'author': 'My Company',
    'website': 'https://example.com',
    'license': 'LGPL-3',               # LGPL-3 / AGPL-3 / OPL-1 / MIT
    'category': 'Sales/Sales',         # Used in apps menu grouping
    'sequence': 100,                   # Order in apps list
    'depends': ['base', 'mail'],       # Module dependencies (always declare all)
    'data': [                          # Files loaded on install/upgrade (server-side)
        'security/ir.model.access.csv',
        'security/security.xml',
        'data/data.xml',
        'views/views.xml',
        'views/menus.xml',
        'report/report.xml',
    ],
    'demo': [                          # Only loaded with demo data
        'demo/demo.xml',
    ],
    'assets': {                        # Frontend assets
        'web.assets_backend': [
            'my_addon/static/src/**/*.js',
            'my_addon/static/src/**/*.xml',
            'my_addon/static/src/**/*.scss',
        ],
    },
    'installable': True,
    'application': False,              # True = appears in Apps menu as main app
    'auto_install': False,             # True = installs when all depends are installed
}
```

## Lifecycle Hook Keys

```python
{
    'pre_init_hook': 'pre_init_hook',   # function in __init__.py, runs before install
    'post_init_hook': 'post_init_hook', # runs after install
    'uninstall_hook': 'uninstall_hook', # runs on uninstall
    'post_load': 'post_load',           # runs on server startup (even without install)
}
```

```python
# __init__.py
def post_init_hook(env):
    env['my.model'].init_default_data()

def uninstall_hook(env):
    env['ir.model.data'].search([('module', '=', 'my_addon')]).unlink()
```

## Version Format

Canonical: `{odoo_series}.{major}.{minor}.{patch}` — e.g., `19.0.1.2.3`

Odoo accepts four spellings (a version that does not start with `<series>.` gets
the series prepended, and what follows the series must be two or three numeric
parts — `adapt_version` in `odoo/modules/module.py`). All four are accepted by
`bump_manifest_version.py`:

| In the manifest  | Example      | Series from              |
| ---------------- | ------------ | ------------------------ |
| `A.B`            | `1.0`        | `ODOO_VERSION`           |
| `A.B.C`          | `1.0.0`      | `ODOO_VERSION`           |
| `<series>.A.B`   | `18.0.0.4`   | the version itself       |
| `<series>.A.B.C` | `18.0.1.2.3` | the version itself       |

Anything else — `1.2.3.4` (four parts, no series), `18.0.1`, six parts — is not
a valid Odoo version and the script refuses it rather than bumping it.

A shorter form is **normalized to the canonical 5 parts first**, written back,
and only then bumped: `18.0.0.4` → `18.0.0.4.0` → `18.0.0.4.1` on a patch bump.
Both steps are printed, so a repo on short versions converges on the canonical
form one commit at a time.

A series-less version (`A.B` / `A.B.C`) takes its series from `ODOO_VERSION`,
the container's Odoo series. With `ODOO_VERSION` unset the script exits
non-zero naming the manifest and the version it read: the series is part of the
module's identity, and the wrong one is worse than a stopped commit. The series
already written in a version always wins over `ODOO_VERSION`, so a 19.0
devcontainer does not re-series an 18.0 client module.

Always bump before committing. Run from module directory:

```bash
python <base directory>/scripts/bump_manifest_version.py
```

## v19 Notes

- `qweb` key **removed** — all templates via `assets` key
- `external_dependencies` still supported: `{'python': ['lxml'], 'bin': ['wkhtmltopdf']}`
- `maintainers` key: list of GitHub usernames (OCA convention)
- `development_status`: `'Alpha'` / `'Beta'` / `'Production/Stable'`
