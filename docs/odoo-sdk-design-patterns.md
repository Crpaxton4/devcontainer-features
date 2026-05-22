# Odoo SDK - Applicable Design Patterns

## Scope

This guide applies only patterns that fit the current SDK shape and do not require changing established public surfaces such as `OdooClient`, `OdooModel`, `OdooQuery`, `OdooExecutor`, or `CommandDispatcher`.

Selection rules
- Prefer patterns that already match the current code.
- Prefer internal patterns over public API expansion.
- Reject patterns that add framework-like machinery without a concrete pressure point.
- Keep the architecture local-tooling friendly and easy to test.

## Patterns That Apply Directly

| Pattern | Status | Current fit | Why it fits this SDK | Surface impact |
|---|---|---|---|---|
| Command | Already in use | `CommandDispatcher` plus command callables | Encapsulates use-case level operations behind callable objects with injected dependencies | None |
| Facade | Already in use | `OdooClient` | Provides one simple entry point over settings, authentication, executor delegation, and model lookup | None |
| Strategy | Already in use | `OdooExecutor` with `OdooRpcExecutor` and test doubles | Lets transport and execution behavior vary without changing model or query code | None |
| Proxy | Already in use | `OdooModel` | Acts as a client-side stand-in for a remote Odoo model and forwards calls to the executor | None |
| Builder | Already in use, lightweight | `OdooQuery` | Builds search options step by step using an immutable fluent interface | None |
| Factory Method | Already in use, lightweight | `CommandDispatcher.register()` factories and `OdooClient.__getitem__()` model creation | Centralizes object creation for commands and model proxies without exposing construction details to consumers | None |
| Adapter | Recommended next | Internal field and value translation layer around `fields_get` and XML-RPC payloads | Hides Odoo wire formats such as many2one tuples, x2many command data, and date strings behind existing read surfaces | Internal only |
| Decorator | Optional next | Wrapper around `OdooExecutor` or a future session object | Adds logging, timing, retry, caching, or redaction behavior without altering the executor interface | Internal only |

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
- Do not move field translation, relation handling, or domain serialization logic into the facade.

### Strategy

`OdooExecutor` is the seam for execution behavior.

Design rule
- New transport behavior should be introduced by another executor-compatible implementation or wrapper, not by branching inside `OdooModel` or `OdooQuery`.

### Proxy

`OdooModel` is best treated as a remote proxy, not a business-service bucket.

Design rule
- Keep model proxies responsible for delegating model operations.
- Avoid packing unrelated business workflows into the proxy.

### Builder

`OdooQuery` already behaves like an immutable builder.

Design rule
- If search configuration grows, extend `OdooQuery` rather than adding many specialized search helper methods to `OdooModel`.
- Preserve cloning and immutability so chaining stays predictable.

### Factory Method

Creation seams already exist and should stay centralized.

Design rule
- Continue constructing command instances through registered factories.
- Continue constructing model proxies in one place rather than scattering proxy creation across consumers.

### Adapter

This is the next pattern with the highest leverage.

Good fit
- Converting many2one tuples to richer local representations.
- Translating x2many command structures.
- Normalizing date and datetime field values.

Design rule
- Add adapters behind current read and metadata APIs.
- Do not change the high-level entry points just to introduce adapters.

### Decorator

Use only for repeated cross-cutting concerns.

Good fit
- Local logging around `execute()`
- Timing or profiling for local tooling
- Retry or redaction wrappers

Design rule
- Wrap executors or sessions when a concern appears in more than one place.
- Do not push logging or retry branches down into every model or query method.

## Patterns To Avoid For Now

| Pattern | Why it does not fit now |
|---|---|
| Abstract Factory | One executor family and one main facade do not justify a larger product-family abstraction yet |
| Mediator | `CommandDispatcher` is a simple registry, not a coordination hub with competing peers |
| Observer | The SDK has no event model that needs subscriptions |
| Singleton | Conflicts with explicit dependency injection and makes testing harder |
| Visitor | There is no stable object graph or AST worth visiting yet |
| Composite | A future domain object may use it, but introducing it now would force a larger public domain redesign |

## SOLID Mapping

| Principle | How these patterns support it in this SDK |
|---|---|
| SRP | Commands own one use case, the facade stays thin, strategies own execution behavior, adapters only translate data, decorators only add cross-cutting behavior |
| OCP | New executors, adapters, or decorators extend behavior without widening `OdooClient` or `OdooModel` |
| LSP | Any `OdooExecutor` implementation should remain substitutable for model and query consumers |
| ISP | Narrow interfaces such as `execute(model, method, ...)` stay easier to satisfy than broad service contracts |
| DIP | High-level command, model, and query code depends on `OdooExecutor` and factories rather than `OdooRpcExecutor` directly |

## Practical Guidance

1. Keep `CommandDispatcher` a registry and dispatch mechanism, not a workflow engine.
2. Keep `OdooClient` as the facade, not as the place where business rules accumulate.
3. Keep `OdooQuery` immutable and builder-like instead of multiplying model helper methods.
4. Add adapters before adding more convenience methods for raw XML-RPC payload shapes.
5. Add decorators only for concerns that clearly repeat, such as logging, retry, or profiling.
6. Prefer constructor injection and registered factories over global state.
