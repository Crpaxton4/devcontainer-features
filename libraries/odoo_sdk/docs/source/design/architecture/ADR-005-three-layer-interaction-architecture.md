# ADR-005 - Three-Layer Interaction Architecture

Status: Accepted

Date: 2026-09-10

## Context

- The SDK grew phase by phase (recordset core, task-tracker state, command
  registry, MCP/TUI/CLI frontends) without a stated layering model. An
  architecture audit ahead of the odoo-dev plugin unification program found
  the de-facto structure is already close to a clean three-layer shape:
  there are zero core→surface edges, zero data→surface edges, the three
  frontends are mutually independent, and the
  transport/client/records/query/fields/env stack has no upward edges.
- The audit also found a bounded set of deviations: ten surface→data
  type-imports (the TUI and MCP server importing `state`/`transport` types
  directly), the CLI duplicating the resync orchestration inline
  (`cli/__main__.py`, which `commands/builtin/resync.py` already owns), two
  data→core imports (the adapters borrowing the sessionization event
  vocabulary), four internal `from odoo_sdk import ...` root-imports that
  formed a real import cycle through the package `__init__` (survivable only
  because of a lazy import in `commands/log_event.py`), and `transport`
  importing `state.config` just for connection settings.
- Nothing enforced any of this, so every new module was free to regress it.
  This ADR freezes the layer model and turns it into machine-checked
  contracts before the follow-up restructuring PRs (#712-#718) start moving
  code.

## Decision

### Layer model

Three layers plus a small shared kernel, defined by *interaction direction*,
not by directory moves (the package layout is unchanged in this ADR):

- **Surface** — frontends that parse input, format output, and dispatch into
  core. They own no business logic and no persistence.
- **Core** — the application layer: the command registry and builtin
  commands, billing/reporting workflows, sessionization transforms, and the
  run maintenance workflows. Core orchestrates data-layer objects.
- **Data** — everything that talks to an external system or owns
  persistence: the RPC transport stack, the recordset/query/fields model of
  Odoo, local SQLite state + config, and the external-source adapters.
- **Shared kernel** — tiny dependency-free helpers and cross-layer
  vocabulary that any layer may import: `_utils` and the `errors` façade
  (plus, once #717 extracts them, connection settings).

### Module assignment

| Module / package | Layer | Notes |
| --- | --- | --- |
| `cli/` | surface | `cli/__main__` is a composition root (named exception) |
| `mcp/` (incl. `mcp/tools`, `mcp/prompts`) | surface | `mcp/__main__` is a composition root (named exception) |
| `tui/` | surface | `tui/__main__` is a composition root (named exception) |
| `commands/` (incl. `commands/builtin`) | core | the registry is the service layer |
| `billing/` | core | timesheet/reporting workflows |
| `sessionization/` | core | pure transforms + frozen event vocabulary |
| `reap.py` | core | orphaned at package root; relocation tracked in #717 |
| `prune.py` | core | orphaned at package root; relocation tracked in #717 |
| `utilities/` | core (provisional) | spans all three layers today; dissolution into `services/` (data), core helpers, and `mcp/prompts` is #717. Classified core so its data-ward imports stay legal until then |
| `skills/` | core | packaged consulting-skill data + `skill_body` accessors (amendment, #712): pure stdlib, no MCP/CLI imports; surfaces read it, nothing below core does |
| `transport/` | data | RPC/JSON-2 executors + canonical Odoo error taxonomy |
| `client/` | data | `OdooClient` session façade |
| `records/` | data | `OdooRecordset` / `Record` |
| `query/` | data | `Domain` / `DomainExpression` |
| `fields/` | data | wire-value normalization |
| `env/` | data | metadata cache |
| `state/` | data | SQLite task-tracker state, `LocalConfig`, FSM errors |
| `adapters/` | data | external-source sync + state persistence adapters |
| `_utils.py` | shared kernel | private helpers, importable from any layer |
| `errors.py` | shared kernel | façade re-exporting the `transport.errors` taxonomy and the `state.models` FSM errors (new in this ADR) |
| `__init__.py` | root façade | public API re-exports; PEP 562 lazy `OdooMCPServer` export (named exception) |

### The five rules

1. **Surfaces are mutually independent.** `cli`, `mcp`, and `tui` never
   import each other, directly or indirectly.
2. **Nothing below a surface imports a surface.** Core, data, and the shared
   kernel never import `cli`/`mcp`/`tui`. The single sanctioned exception is
   the root façade's PEP 562 lazy `OdooMCPServer` export in
   `odoo_sdk/__init__.py`.
3. **Data does not import core.** Two permanent exceptions: the adapters
   import the sessionization event *vocabulary* (`EventType`/`RawEvent`,
   `SessionizationConfig` — pure frozen models, no behaviour).
4. **Surfaces reach data only through core.** Direct surface→data imports
   are forbidden; the three composition roots (`cli/__main__`,
   `mcp/__main__`, `tui/__main__`) are permanent exceptions because someone
   has to construct the object graph. Every other current violation is a
   frozen baseline-debt entry annotated with the issue that deletes it.
5. **No internal root-imports.** Inside `src/odoo_sdk`, `from odoo_sdk
   import ...` (and bare `import odoo_sdk`) is banned: it re-enters the
   partially initialized package and is how the error-import cycle formed.
   Error types are imported from `odoo_sdk.errors` or their canonical
   module.

### Enforcement

- Rules 1-4 are [import-linter](https://import-linter.readthedocs.io/)
  contracts under `[tool.importlinter]` in `pyproject.toml`. Rule 5 is a
  grep-style check in `tools/static_analysis.py`
  (`check_no_internal_root_imports`). Both run under the existing
  `make static` target (`check_layering` invokes `lint-imports`), which the
  `odoo-sdk-quality` workflow already calls — no workflow changes.
- import-linter is grimp-based and sees lazy/function-body and
  `TYPE_CHECKING` imports, so deferred imports cannot dodge the contracts
  (the lazy `reap` import inside `commands/log_event.py` is visible to the
  graph as a legal core→core edge).
- Every `ignore_imports` entry is either commented `permanent` (a named
  exception in this ADR) or carries the GitHub issue that deletes it, and
  `unmatched_ignore_imports_alerting = "error"` makes a stale entry fail the
  build — the exception list can only shrink.
- **Adding a new exception requires amending this ADR in the same PR.** A
  bare pyproject edit that grows an ignore list without an ADR-005 amendment
  is a review-rejectable change.
- Endgame (#718): once the debt entries are gone, the contracts flip to
  `exhaustive = true` so an unclassified new module fails CI.

### Baseline debt register

Frozen at contract-introduction time; each row names the stacked PR that
retires it.

| Debt | Fixed by |
| --- | --- |
| `cli/__main__` duplicates resync orchestration inline and imports `adapters` for it | #717 |
| `tui/app` imports `LocalConfig`/`LocalStateClient`/`EventRecord` from `state` | #716 (config injection) + #718 (StateStore port) |
| `tui/app` imports `OdooError` from `transport.errors` directly | #718 (adopt `odoo_sdk.errors`) |
| `tui/triage`, `tui/evidence`, `tui/export` import `state` types; `tui/timeline` imports `state.db` | #718 |
| `tui/export` imports `adapters` directly | #718 |
| `mcp/server` imports FSM error types from `state.models`; `mcp/tools/start_task` imports `TaskState` | #718 |
| `transport` imports `state.config` for connection settings (data→data, no contract broken, still wrong direction: transport is below state) | #717 (settings extraction into the shared kernel) |
| `utilities/` spans all three layers | #717 (dissolve into `services/` + core + `mcp/prompts`) |
| Composition roots construct the graph inline (permanent exceptions today) | #716 (`bootstrap.py` single composition root, then the entries are deleted) |
| Data modules import private `_`-named helpers from the shared kernel (`_is_sequence`, `_is_null_wire_value`, `_dedup_field_names` from `_utils`) | accepted — the kernel is intra-package by design; revisit naming in #718 |

### The `errors` façade

`odoo_sdk/errors.py` (shared kernel) re-exports the Odoo error taxonomy from
`transport/errors.py` and the task-tracker FSM errors from
`state/models.py`. It exists so that core and surface modules can name error
types without importing the package root (the cycle) and without importing
data modules directly (rule 4). The four former root-import sites
(`utilities/attachments`, `utilities/mail_status`, `mcp/server`,
`billing/unlogged_time`) now import from it; the canonical classes are
unchanged and identical objects (`odoo_sdk.OdooError is
odoo_sdk.errors.OdooError`).

## Rejected alternatives

- **Big-bang `surface/`/`core/`/`data/` directory move.**
  Rejected for this ADR: 124 test files deep-import the current module
  paths, and the whole point of the baseline is an untouched test suite as
  the regression oracle. Deferred to the next major version; #717/#718 do
  targeted moves with re-export shims instead.
- **Blanket aliasing** (shim packages `odoo_sdk.surface.*` etc. over the
  current layout). Rejected: two names for every module, no enforcement
  value, and grimp would see the real edges anyway.
- **Enforcement-only** (add the linter, skip the ADR). Rejected: the ignore
  list is meaningless without the layer definitions, the named exceptions,
  and the amendment policy that keeps it shrinking.
- **A fourth domain-models layer** (splitting frozen dataclasses out of
  core/data into `domain/`). Rejected: the domain vocabulary is small and
  lives naturally where it is (`sessionization.models`, `state.models`,
  `records`); a fourth layer adds a boundary with nothing behind it. #718
  promotes the recordset types into core instead.
- **Registry as the sole surface→core gateway** (machine-enforcing that
  surfaces call only `Registry`). Stays a convention: the registry *is* the
  service layer (ADR-004), but surfaces legitimately import command classes
  and formatting helpers; a module-level contract cannot express
  "call-through-registry" without banning those.

## Deliberately not adopted

Patterns considered during the audit and left out on purpose:

- **Unit of Work** — no multi-aggregate transaction exists; every command
  is a single RPC or a single SQLite transaction.
- **Message bus** — the command `Registry` already is the dispatcher; a bus
  would be a second dispatcher in front of the first.
- **DI container** — the object graph is a handful of constructors; a
  container hides the wiring from the import linter it was just given.
- **Repository atop `RpcClient`** — one port per external system suffices;
  a repository layer over the transport port would re-invent `OdooRecordset`.
- **Renaming `commands/` to `service_layer/`** — the registry IS the
  service layer; the rename would churn every import for a synonym.
- **Full DDD vocabulary** (aggregates, entities, value objects) — the
  domain here is transformational, not stateful; frozen dataclasses + pure
  functions already say everything the vocabulary would.
