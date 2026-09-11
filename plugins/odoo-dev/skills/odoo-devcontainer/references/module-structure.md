# Module Structure

Standard Odoo module layout:

- `__manifest__.py` — module metadata (see `manifest.md`)
- `__init__.py` — python imports
- `models/` — business logic (ORM models)
- `views/` — XML views and menus
- `security/` — access rules (`ir.model.access.csv`) and record rules
- `data/` — data files (sequences, config, email templates)
- `demo/` — demo data
- `static/` — web assets (JS, CSS, images)
  - `static/src/` — source JS/XML/SCSS
  - `static/description/` — app icon (`icon.png`) and screenshots
- `tests/` — test cases
- `i18n/` — translation `.pot`/`.po` files
- `controllers/` — HTTP route controllers
- `wizards/` — transient models (temporary dialogs)
- `reports/` — QWeb report templates and `ir.actions.report` records
