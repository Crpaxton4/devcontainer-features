# odoo_dev_core

The logic layer of the odoo-dev suite (LOGIC layer). It holds the command
registry, the builtin commands, billing and sessionization. It reaches the
outside world only through ports. Every app drives the same registry.

## Public API

`odoo_dev_core` exports `CommandRegistry`, `CommandService`, `CommandSpec`,
`Services`, `command` and `default_registry`. Modules:

| Module | Holds |
|---|---|
| `registry.py` | `CommandSpec`, `CommandRegistry`, `CommandService`, the `@command` decorator. |
| `ports.py` | The port Protocols and the `Services` bundle. |
| `builtins/` | One module per builtin command. |
| `billing/`, `sessionization/`, `services/`, `workflows/` | Timesheet billing, session derivation, thin Odoo wrappers, resync. |
| `telemetry.py` | Usage events and the error boundary of the surfaces. |

## Ports

An app bootstrap builds one adapter per port and passes them in `Services`.

| Port | Adapter |
|---|---|
| `RpcClient` | `odoo_client` |
| `StateStore` | `odoo_dev_data.events` |
| `GitGateway`, `IssueTracker`, `ChatterGateway` | `odoo_dev_data.adapters.{git,github,odoo}` |
| `SettingsView` | `odoo_dev_data.config` |
| `JobsPort` | `odoo_dev_data.jobs` |
| `RepoMapPort`, `TaskRecords`, `Clock` | the app bootstrap |

## Write a builtin

Add a module in `builtins/`. Define a frozen `Args` dataclass and a function
`handler(services, args)`. Decorate it with `@command`:

```python
@command("get_uid", args=Args, description="...", gated=False, surfaces=frozenset({"cli", "mcp"}))
def handler(services: Services, args: Args): ...
```

`default_registry()` imports every builtin. `surfaces` names where a command
shows (`cli`, `mcp`). `gated=True` flags a command that reads administrative
data. `get_models` is the one gated builtin.

## Dependencies

`odoo_client`, `odoo_dev_data`. The import-linter contracts forbid imports of the
data constructors (`odoo_dev_data.config`, `jobs`, `db` and others) and of any app.

## Test

```
uv run --package odoo_dev_core pytest libraries/odoo_dev_core/tests
```
