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
| `tracking/` | core | local run/session tracking helpers (amendment, #717): `env`, `runs`, `stats`, `checkpoint` from the dissolved `utilities/` plus the relocated `reap` and `prune`; #718 adds `models` (the promoted tracker vocabulary — see below) and `events` (core door to the raw-event read) |
| `reap.py` | shim | deprecation shim aliasing `tracking/reap.py` (#717); excluded from contracts |
| `prune.py` | shim | deprecation shim aliasing `tracking/prune.py` (#717); excluded from contracts |
| `services/` | data | Odoo-facing service helpers (amendment, #717): `odoo_helpers`, `activities`, `attachments`, `knowledge`, `mail_status`, `logged_lines` from the dissolved `utilities/` — every module takes an `OdooClient` and talks to Odoo |
| `utilities/` | shims + shared | dissolved by #717: per-module deprecation shims for every moved path, plus the genuinely shared pure `utilities/html.py`, which stays. Excluded from contracts (shims re-export across layers by design) |
| `settings.py` | shared kernel | connection-settings value object + validators extracted from `state/config.py` (amendment, #717) so transport stops importing the state layer; `state.config` re-exports them unchanged |
| `skills/` | core | packaged consulting-skill data + `skill_body` accessors (amendment, #712): pure stdlib, no MCP/CLI imports; surfaces read it, nothing below core does |
| `transport/` | data | RPC/JSON-2 executors + canonical Odoo error taxonomy |
| `billing/logged.py` | core | core door to the Odoo logged-hours read (amendment, #718) |
| `client/` | data | `OdooClient` session façade |
| `records/` | core | `OdooRecordset` / `Record` — promoted to core domain types (amendment, #718; see the recordset decision below) |
| `query/` | core | `Domain` / `DomainExpression` — promoted with `records/` (amendment, #718) |
| `fields/` | data | wire-value normalization |
| `env/` | data | metadata cache |
| `state/` | data | SQLite task-tracker state + `LocalConfig`; `state/models.py` is now a sanctioned non-warning alias of the promoted `tracking/models.py` (amendment, #718) |
| `adapters/` | data | external-source sync + state persistence adapters, one package per external system since #718 (`git/`, `github/`, `odoo/`, `google/`, `state/`; see the port-set amendment) |
| `_utils.py` | shared kernel | private helpers, importable from any layer; also `format_chatter` (amendment, #717 — see below) |
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
- Endgame (#718) — DONE: the rule-4 debt entries are gone and a sixth
  contract (`layers`, `exhaustive = true` over the `odoo_sdk` container)
  requires every top-level module to claim a layer, with
  `exhaustive_ignores` naming only the composition root (`bootstrap`), the
  `errors` kernel façade, and the shims (`utilities`, `prune`, `reap`). An
  unclassified new module now fails CI (verified by adding a scratch
  module and watching `lint-imports` reject it).

### Baseline debt register

Frozen at contract-introduction time; each row names the stacked PR that
retires it.

| Debt | Fixed by |
| --- | --- |
| `cli/__main__` duplicates resync orchestration inline and imports `adapters` for it | #717 — DONE: the CLI dispatches the registry `ResyncCommand` with an injected local-first puller table and reaches the pullers/event-source vocabulary through `commands/builtin/resync` and `commands/log_event` |
| `tui/app` imports `LocalConfig`/`LocalStateClient`/`EventRecord` from `state` | #716 (config injection) + #718 — DONE: `TuiDeps` is typed against the `StateStore`/`SettingsView` ports and `EventRecord` comes from the promoted `tracking.models` |
| `tui/app` imports `OdooError` from `transport.errors` directly | #718 — DONE: imports the kernel façade `odoo_sdk.errors` |
| `tui/triage`, `tui/evidence`, `tui/export` import `state` types; `tui/timeline` imports `state.db` | #718 — DONE: the vocabulary (`EventRecord`, `SessionWindow`, `format_repo_label`) is promoted to `tracking.models`; `tui/export` is typed against the `StateStore` port |
| `tui/export` imports `adapters` directly | #718 — DONE: reads raw events through the core door `tracking.events` |
| `mcp/server` imports FSM error types from `state.models`; `mcp/tools/start_task` imports `TaskState` | #718 — DONE: `TaskState` comes from the promoted `tracking.models` (the `mcp/server` half was already fixed by the errors façade) |
| `transport` imports `state.config` for connection settings (data→data, no contract broken, still wrong direction: transport is below state) | #717 — DONE: `odoo_sdk/settings.py` (shared kernel) owns the value object; transport and client import it, `state.config` re-exports it |
| `utilities/` spans all three layers | #717 — DONE: dissolved into `services/` (data), `tracking/` (core), `mcp/prompts/messages.py` (surface content), and the shared kernel (`format_chatter`); `utilities/html.py` stays as shared code |
| `tui/app` imports the Odoo-reading `services.logged_lines` helper directly | #718 — DONE: reads through the core door `billing.logged` (the edge was newly *visible* debt from #717's reclassification, not newly created) |
| Composition roots construct the graph inline (permanent exceptions today) | #716 (`bootstrap.py` single composition root, then the entries are deleted) |
| Data modules import private `_`-named helpers from the shared kernel (`_is_sequence`, `_is_null_wire_value`, `_dedup_field_names` from `_utils`) | accepted, permanently (#718 decision): the kernel is intra-package by design and the names are deliberately private to the package — renaming them public would advertise stability the kernel does not promise |

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

### Composition root and configuration (amendment, #716)

- **`bootstrap.py` is the single composition root.** The rule-4 "someone has
  to construct the object graph" exceptions for the three `__main__` modules
  are retired: `odoo_sdk/bootstrap.py` now assembles the default graph
  (`bootstrap(*, client=None, state=None, config=None) -> Registry`), and the
  entrypoints shrink to parse → bootstrap → dispatch → format. The module is
  deliberately outside every contract source list — it is the ONE module
  allowed to name every layer — and a new rule-5 `forbidden` contract ("only
  the entrypoints import the composition root") guarantees nothing else
  imports it. The entrypoints obtain the concrete constructors they still
  need by name (`OdooClient`, `LocalConfig`, `LocalStateClient`) from the
  composition root's re-exports rather than from the data layer, so
  `bootstrap` is their only below-core dependency.
- **Config is injection-only in production.** `bootstrap()` loads
  `LocalConfig` at most once per graph and injects it into every command via
  the `Registry`; no production path reaches `Command.config`'s lazy
  `LocalConfig.load()` fallback any more. Of the two candidate shapes for the
  command base — *remove the fallback and raise when un-injected* vs. *a
  core-owned frozen `Settings` value object* — **neither was adopted**, and
  the fallback survives as a documented test-convenience escape hatch: the
  untouched-suite regression oracle pins the fallback itself
  (`tests/test_command_registry/test_command_protocol.py::
  test_config_lazily_loaded_when_absent` asserts that an un-injected
  `Command.config` calls `LocalConfig.load()` from `commands/command.py` and
  caches it), so both removal and a `Settings` delegation would have required
  editing frozen tests. A `Settings` dataclass additionally buys nothing
  while the fallback is pinned: core reads `LocalConfig`'s behavior
  properties in only three modules, and a second config type would fork the
  peer-dependency contract the registry injects. Revisit removal when the
  command-protocol contract tests are next allowed to move (#718 endgame).

### Dissolution of `utilities/`, settings extraction, and shims (amendment, #717)

- **`utilities/` is dissolved by interaction direction.** The Odoo-facing
  helpers (each takes an `OdooClient` and issues real calls) moved to the new
  data package `services/`; the local run/session helpers moved to the new
  core package `tracking/`, which also absorbed the root-orphaned `reap.py`
  and `prune.py`; the `implement_task` prompt builders moved to
  `mcp/prompts/messages.py` (the strings ARE the MCP prompt — surface
  content). Two classification corrections surfaced by the move:
  `format_chatter` is a pure primitives-only renderer needed by both the MCP
  prompt builder (surface) and the chatter services (data), so its only legal
  home is the shared kernel (`_utils.py`, re-exported by
  `services/odoo_helpers.py`); and `utilities/html.py` is genuinely shared
  pure text conversion, so it stays in place rather than moving into a layer.
- **Connection settings live in the shared kernel.** `odoo_sdk/settings.py`
  now owns `OdooConnectionSettings`, its validators, and
  `DEFAULT_TIMEOUT_SECONDS`; `transport/` and `client/` import it, and
  `state/config.py` re-exports everything unchanged so every historical
  `odoo_sdk.state.config` import (including the feature tests outside this
  repo that pin `odoo_sdk.state.db` / `state.config`) keeps working.
  `LocalConfig` remains the single resolver; the one seam that touches it —
  `OdooConnectionSettings.from_sources` — imports it lazily inside the call
  so the kernel module stays import-time dependency-free.
- **CLI resync has one writer.** `cli/__main__.py` no longer duplicates the
  resync orchestration inline: it dispatches the registry's `ResyncCommand`
  (source selection, range parsing, and the per-source summary shape live
  only there) while injecting a CLI-specific puller table via the command's
  new keyword-only `pullers` seam. The table preserves the CLI's pinned
  per-source semantics exactly — git/github run with no Odoo client, the
  odoo puller stays behind the lazy capability guard, gcal/gmail keep the
  range-ignored annotation — and resolves the puller names through
  `cli.__main__` module globals at call time, so the frozen CLI tests'
  patch points (`cli.__main__.sync_git_log`, …) still intercept every call.
  The CLI's `adapters` imports are gone: pullers and Google error types come
  through `commands/builtin/resync`, the event-source vocabulary through
  `commands/log_event`, retiring the `cli.__main__ -> adapters` baseline
  entry.
- **Shim policy.** Every moved module leaves a shim at its old path:
  `sys.modules`-aliasing modules (the old path warns once on import, then IS
  the relocated module, so attribute patches through the old path keep
  reaching canonical code — the property the 124 deep-importing frozen test
  files rely on), plus a PEP 562 `__getattr__` in `utilities/__init__.py`
  for the package-level re-exports. All shims emit `DeprecationWarning`
  (`stacklevel=2`), are kept for **at least two minor releases**, are never
  removed in the release that introduced them, and are **excluded from the
  import-linter contract lists** — a shim re-exports across layers by
  design, so listing it would only force ignore entries that restate its
  job. The canonical homes are listed and enforced instead.
- **Baseline movement.** Deleted: `cli.__main__ -> adapters` (#717). Added
  (with this amendment, per the exception policy): `tui.app ->
  services.logged_lines` — newly *visible*, not newly created; the TUI has
  always called the Odoo-reading logged-hours helper directly, but the edge
  was invisible while `utilities/` was classified core. #718's port work
  retires it.

### Port set, recordset promotion, and exhaustive mode (final amendment, #718)

- **The consumer-side port set is complete.** `commands/protocols.py` now
  defines one structural Protocol per external system the command layer
  drives, each derived strictly from what the consumers call today:

  | Port | Concrete adapter | Adapter package |
  | --- | --- | --- |
  | `RpcClient` | `OdooClient` | `client/` + `transport/` |
  | `StateStore` | `LocalStateClient` | `state/` (+ `adapters/state`) |
  | `GitGateway` | `sync_git_log` module surface | `adapters/git` |
  | `IssueTracker` | `sync_github` module surface | `adapters/github` |
  | `CalendarGateway` | Google puller module surface | `adapters/google` |
  | `SettingsView` | `LocalConfig` (read-only view) | `state/config` |

  `StateStore` carries the 39 members core, billing/tracking, and the TUI
  driver actually call; the store's ingest-only members (`add_event_dedup`,
  `get_event`, `get_events`, `update_timesheet_id`) are deliberately absent —
  only the data-side resync adapters use them, and an adapter talking to its
  own store needs no port. The gateway ports are satisfied by the adapter
  *packages themselves* (PEP 544 module-implements-protocol), so the frozen
  module-function pullers need no wrapper objects. Odoo task chatter gets no
  fifth gateway: its puller reaches Odoo through the existing `RpcClient`.
  Wiring: `bootstrap()` builds the concrete `RpcClient`/`StateStore`/config
  instances; core's default gateway→adapter binding is
  `commands/builtin/resync._SYNC_DISPATCH`, overridable per entrypoint
  through the command's `pullers` seam (#717). Conformance is pinned by the
  additive `tests/test_commands/test_ports.py`.
- **Adapters: one package per external system** (Netflix-Dispatch style):
  `adapters/git`, `adapters/github`, `adapters/odoo`, `adapters/google`,
  `adapters/state`. Two are physical moves with shims per the shim policy:
  `state_persistence.py` → `adapters/state/persistence.py` (warning
  `sys.modules` alias at the old path) and the whole Google section of
  `external_sync.py` → `adapters/google/sync.py` (its tests inject
  transports rather than patching module attributes, so it could move; the
  old module re-exports every public and test-read Google name). The
  git/GitHub/Odoo-chatter implementations remain *physically* in
  `external_sync.py` behind their packages' façades — **blocked, concretely**:
  the frozen adapter tests patch the sections' shared seams on that module
  object (`patch.object(external_sync, "_run_capture"/"_gh_json"/
  "_discover_git_repos", ...)` in `tests/test_adapters/test_external_sync.py`),
  and relocated code would resolve those names in its own globals and escape
  the patches. The per-system packages are the canonical import surface
  (core imports through them); the bodies follow when the adapter tests are
  next allowed to move. Cross-section pure helpers (`_extract_task_ids`,
  `_parse_iso_utc`) moved to `adapters/_shared.py` to break the
  google↔external_sync cycle.
- **Recordset decision — executed as promotion by reclassification.**
  `records/` (`OdooRecordset`/`Record`) and `query/`
  (`Domain`/`DomainExpression`) are now **core** in every contract: the
  public API is explicitly recordset-first, so `RpcClient.__getitem__`
  naming `OdooRecordset` is a port returning a core domain type, not an
  adapter leak. The coupling assessment found exactly one downward
  dependency — `records.recordset` imports the abstract `OdooExecutor`
  contract and the `guarded_execute` chokepoint from `transport/executor` —
  which is the sanctioned core→data direction (the RPC plumbing itself
  stays data-side behind that seam), so no file had to move and no
  untouchable test was at risk. The one upward edge this creates —
  `client.client -> records.recordset`, the adapter constructing the domain
  type its port returns — is a named permanent exception (hexagonal
  adapters import the domain by design). Physical relocation into a
  `core/`-style directory stays deferred with the rest of the big-bang move
  (124 deep-importing test files).
- **Tracker vocabulary promoted.** `state/models.py` (the `TaskState` FSM,
  `TaskRun`, `EventRecord`, `SessionWindow`, `session_key`, the FSM errors)
  moved wholesale to `tracking/models.py`, joined by the absent-repo display
  vocabulary (`format_repo_label`, `AGENTLESS_REPO*`) from `state/db.py`.
  `state/models.py` remains as a **non-warning** `sys.modules` alias — it is
  eagerly imported by `odoo_sdk.state.__init__` (the supported public
  re-export path), so a `DeprecationWarning` there would fire on every
  `import odoo_sdk.state`; the path is sanctioned, not deprecated. Its single
  re-export edge (`state.models -> tracking.models`) is the one shim that
  cannot be unlisted (it lives inside the listed `state` package) and is an
  explicit permanent ignore. The `errors` kernel façade keeps importing the
  FSM errors *via the alias* on purpose: importing `tracking.models`
  directly would thread a new data→core chain through every data-layer
  consumer of the façade (`services -> errors -> tracking`), which rules
  3/6 rightly reject.
- **Exhaustive mode is ON** (rule 6): a `layers` contract over the
  `odoo_sdk` container with `exhaustive = true` — every top-level module
  must claim a layer or be a named `exhaustive_ignores` exception
  (`bootstrap`, `errors`, `utilities`, `prune`, `reap`). Verified by adding
  a scratch module and watching `lint-imports` fail. Within-layer siblings
  use the non-independent `:` form (surface independence stays rule 1's
  job); the contract's `ignore_imports` restate only the named permanent
  upward edges plus the settings kernel's lazy resolver seam
  (`settings -> state.config`, visible to grimp as a function-body import).
- **Rule 4's baseline-debt register is EMPTY.** All nine remaining
  surface→data entries are retired (see the updated register above); the
  contract now carries no ignore list at all. Surfaces reach data only
  through core, the ports, or the kernel façades.
- **`Command.config` lazy fallback — status unchanged, still pinned.** The
  #716 amendment deferred its removal to this issue; re-examined here, the
  untouchable regression oracle still pins the fallback itself
  (`tests/test_command_registry/test_command_protocol.py::
  test_config_lazily_loaded_when_absent` asserts an un-injected
  `Command.config` calls `LocalConfig.load()` and caches it), so it
  survives as the documented test-convenience escape hatch. Production
  remains injection-only via `bootstrap()`. Revisit only when the
  command-protocol contract tests are allowed to move; with the port set
  complete there is no architectural pressure to do so sooner — the
  fallback is core lazily constructing a data-layer default, exactly like
  `Command.state`'s `LocalStateClient()` fallback, and both are legal
  core→data edges.

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
