# Module `migrations/` scripts

Scope: port of YOUR custom module renamed/moved field, model, or xmlid, existing DBs carry old data — ship migration scripts with port. Writing scripts = code work (porting agent scope). RUNNING database upgrade = human-run; never attempt.

## Layout & loader (odoo/modules/migration.py, 18.0/19.0)

```
my_module/
├── __manifest__.py              # "version": "19.0.1.1.0"
└── migrations/                  # "upgrades/" is also scanned and preferred since Odoo 13
    └── 19.0.1.1.0/
        ├── pre-10-rename.py
        ├── post-recompute.py
        └── end-cleanup.py
```

- Each `pre-*.py` / `post-*.py` / `end-*.py` must define `migrate(cr, version)`. Loader checks signature: exactly two positional params named `cr`/`_cr` and `version`/`_version`.
- `version` arg = module version currently installed in DB (version upgraded FROM).
- Folder name: full `19.0.1.1.0` (server series + module version) or short `1.1.0` (loader prepends running server series). Short-form folders NOT re-executed when same module version crossed again on later server series.
- Folder scripts run iff `installed_version < folder_version <= manifest_version`.
- Special folder `0.0.0` runs on every version change: first in `pre`, last in `post`/`end`.
- Within phase, files execute in lexical filename order; `tests/` folder and names not matching version pattern skipped.

## Phases

| Prefix  | Runs                                              | Use for |
|---------|---------------------------------------------------|---------|
| `pre-`  | before module loaded/updated                      | raw SQL / util helpers; move data out of way before ORM touches schema (renames here) |
| `post-` | after module + dependencies updated               | ORM work on new schema — `env = util.env(cr)` (superuser Environment) |
| `end-`  | after ALL modules loaded and updated              | cross-module cleanup, final checks |

## When a script is required

| Change during port              | Script needed? | Why |
|---------------------------------|----------------|-----|
| Field renamed                   | Yes (`pre`)    | ORM creates new column empty; module-data cleanup unlinks stale `ir.model.fields` row, drops old column (`ALTER TABLE .. DROP COLUMN .. CASCADE`, base `ir_model.py`) ⇒ data loss |
| Model renamed                   | Yes (`pre`)    | new table created empty; old table/rows orphaned |
| Xmlid renamed / moved module    | Yes (`pre`)    | `noupdate=0`: old record deleted by end-of-upgrade cleanup (`_process_end`), references break; `noupdate=1`: old record survives ⇒ duplicate |
| New field, new model, view/code-only change | No | ORM handles schema additions; nothing to carry |
| Fresh installs                  | Never          | migrations run only on upgrade of installed module |

## Helper libraries

### odoo/upgrade-util — Odoo's own, used for its standard-module upgrade scripts

Install: `pip install git+https://github.com/odoo/upgrade-util@master` (odoo.sh: add
`odoo_upgrade @ git+https://github.com/odoo/upgrade-util@master` to `requirements.txt`).
Then `from odoo.upgrade import util`.

| Helper | Purpose |
|--------|---------|
| `util.rename_field(cr, model, old, new)` | rename column + update references |
| `util.remove_field(cr, model, fieldname)` | remove field + references (drops column) |
| `util.rename_model(cr, old, new, rename_table=True)` | rename model + table + references |
| `util.remove_model(cr, model, drop_table=True)` | remove model and its data |
| `util.merge_model(cr, source, target)` | fold one model into another |
| `util.rename_xmlid(cr, old, new, noupdate=None, on_collision="fail")` | `"module.name"` → `"module.name"` |
| `util.remove_record(cr, name)` | delete record by xmlid |
| `util.remove_view(cr, xml_id=None, view_id=None)` | remove view safely (inherits handled) |
| `util.ref(cr, xmlid)` | res_id for xmlid, or None |
| `util.env(cr)` | superuser `Environment` from bare cursor |

### OCA openupgradelib — `pip install openupgradelib`, `from openupgradelib import openupgrade`

- `openupgrade.rename_fields(env, [(model, table, old, new)])` — full field rename (pre)
- `openupgrade.rename_columns(cr, {table: [(old, new)]})` — SQL column rename only (pre)
- `openupgrade.rename_models(cr, [(old, new)])`, `openupgrade.rename_xmlids(cr, [(old, new)])`
- `openupgrade.logged_query(cr, query, args=None)` — execute + log rowcount
- `@openupgrade.migrate()` — decorator; passes superuser `env` instead of `cr` (v10+ default), wraps script in savepoint

Choosing: upgrade-util = Odoo's own; openupgradelib = OCA's (standard in OCA/OpenUpgrade modules). Either works inside module migration scripts if package importable at upgrade time.

## Minimal example: field `old_name` → `new_name`, porting 18.0 → 19.0

Manifest bumped `"18.0.1.0.0"` → `"19.0.1.0.0"`. Folder `19.0.1.0.0` triggers: DB still holds `18.0.1.0.0` and `18.0.1.0.0 < 19.0.1.0.0 <= manifest`. Folder version must equal (or precede) new manifest version — folder higher than manifest never runs.

```
my_module/migrations/19.0.1.0.0/pre-rename-old-name.py
```

```python
def migrate(cr, version):
    try:
        from odoo.upgrade import util
    except ImportError:
        # fallback: keeps the data, but does not fix references/filters
        cr.execute("ALTER TABLE my_model RENAME COLUMN old_name TO new_name")
    else:
        util.rename_field(cr, "my.model", "old_name", "new_name")
```

## Gotchas

- Scripts run ONLY on upgrade (`-u my_module` / DB upgrade) of already-installed module — never on fresh install (`state == 'to install'` explicitly skipped by loader).
- Multiple matching version folders run in version order; within one folder, lexical file order — prefix numbers (`pre-10-...`, `pre-20-...`) force ordering.
- Wrong `migrate` signature (extra params, renamed args) raises at upgrade time — loader enforces.
- Prefer idempotent scripts (guard with `IF EXISTS` / column checks): failed-and-retried upgrade attempt may execute script again.
- OCA convention: porting to new series, delete previous series' `migrations/` folder (OCA migration guide: "Remove any possible migration script from previous version").