# odoo_tui

The terminal UI app of the odoo-dev suite (UI layer). It installs the `odoo-tui`
console script (`odoo_tui.__main__:main`, also `python -m odoo_tui`). It shows
sessions over a date window as per-lane timeline bars. From there you can export,
triage events, review and bill sessions to Odoo.

## Layout

| Module | Holds |
|---|---|
| `app.py` | The pure state and transitions (`AppState`, `TuiDeps`). No terminal needed. |
| `textual_app.py` | The Textual app, screens and key bindings. |
| `timeline.py`, `meter.py`, `window.py` | Rendering and window math. |
| `triage.py`, `review.py`, `evidence.py`, `export.py` | The screens' logic. |
| `bootstrap.py` | The composition root: builds the `CommandService`. |

Session detection and triage go through registry commands (`query_sessions`,
`assign_event`), so the CLI and the MCP server share the same operations.

## Dependencies

`odoo_dev_core`, `textual`.

## Test

```
uv run --package odoo_tui pytest apps/odoo_tui/tests
```
