# CLI

`odoo` on PATH. Config pre-loaded.

## Common Ops

```bash
# Start server (foreground)
odoo

# Install module(s)
odoo -i module_name --stop-after-init

# Upgrade module(s)
odoo -u module_name --stop-after-init

# Install multiple
odoo -i mod1,mod2,mod3 --stop-after-init

# Upgrade all
odoo -u all --stop-after-init
```

## En-masse (all modules in cwd)

Run from the repo/addons root (e.g., `/mnt/extra-addons`):

```bash
odoo -i $(bash <base directory>/scripts/list_modules.sh) --stop-after-init
odoo -u $(bash <base directory>/scripts/list_modules.sh) --stop-after-init
```

## Database Management

```bash
odoo db drop <dbname>                  # delete DB + filestore
odoo db dump <dbname> [dump_path]      # dump DB w/ filestore (default: stdout)
odoo db load <dbname> <dumpfile>       # restore dump into DB
odoo db load <dbname> <dumpfile> -f    # force: drop existing DB first
odoo db load <dbname> <dumpfile> -n    # neutralize after restore
odoo db duplicate <src> <dst>
odoo db rename <old> <new>
```

Default DB name: `odoo` (set in odoo.conf).

## Testing

```bash
# Test specific module
odoo --test-enable --test-tags /module_name --stop-after-init -u module_name

# Test w/ tags
odoo --test-enable --stop-after-init -u module_name --test-tags tag_name
```

## Other Subcommands

```bash
odoo scaffold <name> [dest]     # generate module skeleton
odoo shell                       # IPython REPL w/ Odoo env
odoo neutralize                  # neutralize DB (v16+)
odoo tsconfig                    # regen tsconfig.json for OWL/JS
odoo populate                    # generate test data (if model supports it)
```

**Shell transaction mode**: shell runs in transaction by default -> changes roll back on exit. Call `env.cr.commit()` for persistence.

## Dev Mode

```bash
odoo --dev all                   # all dev features below
odoo --dev xml                   # read QWeb templates from xml files, skip DB cache
odoo --dev reload                # restart server on python file changes
odoo --dev qweb                  # break on t-debug='debugger' in QWeb
```

Combine with commas: `odoo --dev reload,xml`
