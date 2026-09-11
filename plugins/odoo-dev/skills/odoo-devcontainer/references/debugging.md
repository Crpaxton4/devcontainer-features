# Debugging

## VSCode Launch Configs

Use preconfigured configs in `.vscode/launch.json`:

- **Debug Odoo** — launch Odoo server with debugger on port 8069
- **Debug Odoo Shell** — IPython shell with debugger attached

### launch.json (actual config)

```json
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Debug Odoo",
            "type": "debugpy",
            "request": "launch",
            "program": "/usr/bin/odoo",
            "args": ["--workers=0"],
            "console": "integratedTerminal",
            "justMyCode": false,
            "env": {
                "PYTHONPATH": "/mnt/extra-addons"
            }
        },
        {
            "name": "Debug Odoo Shell",
            "type": "debugpy",
            "request": "launch",
            "program": "/usr/bin/odoo",
            "args": ["shell", "--workers=0"],
            "console": "integratedTerminal",
            "justMyCode": false
        }
    ]
}
```

> **`--workers=0` is required** — multi-worker mode spawns subprocesses that the debugger cannot attach to.

## Attach to Running Process

If Odoo is already running (e.g., started by the devcontainer):

```json
{
    "name": "Attach to Odoo",
    "type": "debugpy",
    "request": "attach",
    "connect": { "host": "localhost", "port": 5678 },
    "justMyCode": false
}
```

Start Odoo with debugpy listener:

```bash
python -m debugpy --listen 5678 --wait-for-client /usr/bin/odoo --workers=0
```

## pdb / ipdb Fallback

Drop a breakpoint inline without VSCode:

```python
import pdb; pdb.set_trace()   # stdlib
import ipdb; ipdb.set_trace() # richer (requires: pip install ipdb)

# Python 3.7+ shorthand
breakpoint()
```

Run Odoo in the integrated terminal with `--workers=0` so stdin is available:

```bash
odoo --workers=0
```

## Common Debug Patterns

```python
# Inspect a recordset in the shell
record = env['my.model'].browse(1)
record.read()                   # all field values as dict
record._fields.keys()           # all field names
record._name                    # model name

# Force recompute
record._recompute_todo.add(record._fields['total'])
record.recompute()

# Log to console from code
import logging
_logger = logging.getLogger(__name__)
_logger.info("Value: %s", record.name)
_logger.warning("Unexpected state: %s", record.state)
```
