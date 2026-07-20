# Odoo SDK - Applicable Design Patterns

> **Reconciliation note (2026-07).** This guide previously listed
> `CommandDispatcher`, `OdooModel`, `OdooQuery`, and `OdooEnv` as classes
> already in use. None of them ever shipped: `grep` for them in `src/odoo_sdk`
> returns nothing. The real command class is `Registry`
> (`odoo_sdk/commands/command_registry.py`), and the ORM-facing roles the other
> three were meant to fill are carried by `OdooRecordset` and
> `DomainExpression`. The pattern rows below have been remapped to the shipped
> types. See the [architecture plan](./odoo-sdk-architecture-plan.md) for the
> same reconciliation across the wider design set.

## Scope

This guide applies only patterns that fit the current SDK shape and do not require changing established public surfaces such as `OdooClient`, `OdooRecordset`, `DomainExpression`, `OdooExecutor`, or the command `Registry`.

Selection rules
- Prefer patterns that already match the current code.
- Prefer internal patterns over public API expansion.
- Prefer narrow typed extension seams over open-ended interception points.
- Reject patterns that add framework-like machinery without a concrete pressure point.
- Prefer additive, local-testable seams over framework-style extension systems.
- Keep the architecture local-tooling friendly and easy to test.

## Patterns That Apply Directly

| Pattern | Status | Current fit | Why it fits this SDK | Surface impact |
|---|---|---|---|---|
| Command | Already in use | `Registry` (`commands/command_registry.py`) plus `Command` subclasses | Encapsulates use-case level operations behind callable objects with injected dependencies | None |
| Facade | Already in use | `OdooClient` | Provides one simple entry point over settings, authentication, executor delegation, and model lookup | None |
| Strategy | Already in use | `OdooExecutor` with `OdooRpcExecutor`, `OdooJson2Executor`, and test doubles | Lets transport and execution behavior vary without changing recordset or domain code | None |
| Proxy | Already in use | `OdooRecordset` | Acts as a client-side stand-in for a remote Odoo model and its records, forwarding calls to the executor | None |
| Builder | Already in use, lightweight | `DomainExpression` | Composes search domains immutably via `AND` / `OR` / `&` / `\|` / `~` rather than mutating a shared builder | None |
| Factory Method | Already in use, lightweight | `Registry.register()` factories and `OdooClient.__getitem__()` recordset creation | Centralizes object creation for commands and model-bound recordsets without exposing construction details to consumers | None |
| Registry (plugin / adapter) | Phase C addition, distinct from the command `Registry` | Narrow plugin and typed-adapter registration | Keeps extension discovery explicit, inspectable, and resettable for local tests without creating framework-style auto-discovery | Internal only |
| Adapter | Now in use internally, expands selectively in Phase C | Internal field and value translation layer around `fields_get`, read-side adaptation, x2many write serialization, and optional typed adapters for selected stable models | Hides Odoo wire formats and enables selective typed ergonomics without widening the top-level API or replacing the dynamic model | Internal only |
| Decorator | Approved Phase C boundary | Wrapper around `OdooExecutor` (a session or policy boundary was rejected — see [ADR-002](./architecture/ADR-002-session-and-transport-boundary.md)) | Adds tracing, timing, retry, timeout, telemetry, or redaction behavior without altering the executor interface | Internal only |

## Current Compatibility Guidance

The public facade story stays stable while the implementation center of gravity remains recordset-first.

- `OdooClient` remains the facade, and is itself the model registry.
- `OdooRecordset` remains the identity-bearing core and owns execution context inline via `with_context` / `with_company`. There is no separate `OdooEnv` object.
- `DomainExpression` remains the canonical domain normalization boundary.
- `OdooClient`, `DomainExpression`, and `OdooRecordset` are the high-level public exports in the recordset-first API.
- There are no `OdooModel` proxy or `OdooQuery` builder compatibility layers: the recordset-first core replaced those planning-era wrappers outright rather than carrying them forward.

## How To Use These Patterns Here

### Command

Keep commands focused on one use case each.

Good fit
- "Get one task"
- "Create a task"
- "Search partners"

Design rule
- Keep orchestration and business intent in commands.
- Keep XML-RPC transport details out of command classes.

### Facade

`OdooClient` should stay thin.

Design rule
- Let `OdooClient` remain the simplified SDK entry point.
- Do not move field translation, relation handling, domain serialization, or context ownership logic into the facade.

### Strategy

`OdooExecutor` is the seam for execution behavior.

Design rule
- New transport behavior should be introduced by another executor-compatible implementation or wrapper, not by branching inside recordset or domain code.
- Keep mapped error classification at the executor boundary so recordsets propagate the same `OdooError` subclasses without local translation.
- Keep the error taxonomy defined in one place (`transport/errors.py`) and re-exported from the package root, rather than redefining error types per call site.

### Proxy

A model-bound `OdooRecordset` is best treated as a remote proxy, not a business-service bucket.

Design rule
- Keep model-bound recordsets responsible for delegating model operations through the domain and executor path.
- Keep the proxy role on `OdooRecordset` rather than reintroducing a separate model-handle type beside it.
- Avoid packing unrelated business workflows into the proxy.

### Builder

`DomainExpression` already behaves like an immutable builder.

Design rule
- Compose domains through `normalize`, `AND` / `OR`, and the `&` / `|` / `~` operators.
- Preserve immutability so composition stays predictable and shareable.
- Route execution through `OdooRecordset` rather than letting domain composition regain ownership of the core behavior.
- Do not reintroduce a mutable fluent query builder as the control path.

### Factory Method

Creation seams already exist and should stay centralized.

Design rule
- Continue constructing command instances through registered factories.
- Continue constructing model proxies in one place rather than scattering proxy creation across consumers.

### Registry

Phase C registries should stay narrow and explicit.

Good fit
- Plugin contract registration for documented hook categories.
- Typed-adapter selection for a small, documented stable model set.

Design rule
- Keep registration explicit, local-runtime friendly, and inspectable in tests.
- Use one registry path shared by recordset-first and compatibility surfaces.
- Do not turn registries into automatic third-party discovery or a general plugin framework.

### Adapter

This pattern is now used internally in Phase B.

Good fit
- Converting many2one tuples to richer local representations.
- Translating `X2ManyCommand` helper values and compatible raw tuples into canonical Odoo x2many command tuples.
- Normalizing date and datetime field values.

Design rule
- Keep read-side adaptation and write-side x2many serialization behind shared metadata-driven boundaries owned by recordsets and envs.
- Keep the helper API small and explicit; do not change the high-level entry points just to introduce adapters.
- Keep Phase C typed adapters opt-in and selective; when no typed adapter applies, the default dynamic behavior remains the supported path.

### Decorator

Use only for repeated cross-cutting concerns.

Good fit
- Local logging around `execute()`
- Timing or profiling for local tooling
- Retry or redaction wrappers

Design rule
- Wrap executors when a concern appears in more than one place.
- Do not push logging or retry branches down into every recordset method.
- Keep Phase C execution policy hooks for tracing, retry, timeout, and local telemetry on the executor boundary rather than in `OdooClient` or `OdooRecordset`.
- Keep the synchronous facade as the default supported path; any async facade remains a later separate decision.

### Narrow Plugin Contracts

Phase C plugin work should use narrow, typed extension seams rather than an open event bus.

Design rule
- Allow plugins only at existing model-specific adaptation, selection, or serialization seams.
- Disallow transport replacement, arbitrary execution-context mutation, domain serialization ownership, and recordset identity takeover.
- Fail fast when a plugin does not satisfy the documented contract instead of silently ignoring incompatible behavior.

## Patterns To Avoid For Now

| Pattern | Why it does not fit now |
|---|---|
| Abstract Factory | One executor family and one main facade do not justify a larger product-family abstraction yet |
| Mediator | The command `Registry` is a simple registry, not a coordination hub with competing peers |
| Observer | The SDK still does not need a general event model; Phase C should use narrow plugin contracts rather than broad subscriptions |
| Singleton | Conflicts with explicit dependency injection and makes testing harder |
| Visitor | There is no stable object graph or AST worth visiting yet |
| Composite | A future domain object may use it, but introducing it now would force a larger public domain redesign |

## SOLID Mapping

| Principle | How these patterns support it in this SDK |
|---|---|
| SRP | Commands own one use case, the facade stays thin, strategies own execution behavior, adapters only translate data, decorators only add cross-cutting behavior |
| OCP | New executors, adapters, or decorators extend behavior without widening `OdooClient` or `OdooRecordset` |
| LSP | Any `OdooExecutor` implementation should remain substitutable for recordset consumers |
| ISP | Narrow interfaces such as `execute(model, method, ...)` stay easier to satisfy than broad service contracts |
| DIP | High-level command and recordset code depends on `OdooExecutor` and factories rather than `OdooRpcExecutor` directly |

## Practical Guidance

1. Keep the command `Registry` a registry and dispatch mechanism, not a workflow engine.
2. Keep `OdooClient` as the facade, not as the place where business rules accumulate.
3. Keep `DomainExpression` immutable and builder-like instead of multiplying recordset helper methods.
4. Add adapters before adding more convenience methods for raw XML-RPC payload shapes.
5. Add decorators only for concerns that clearly repeat, such as logging, retry, or profiling.
6. Use registries only when they keep one canonical plugin or adapter path and can be reset cleanly in tests.
7. Prefer constructor injection and registered factories over global state.
