# Feature Name

OdooEnv Environment Root

> **Status: superseded (2026-07 audit).** `OdooEnv` was built during Phase A and then removed again as superseded and un-exported (PR #161). No `OdooEnv` type exists in `src/odoo_sdk/`. Execution context now lives directly on `OdooRecordset`, which carries the executor, a defensively copied context dict, and the shared `MetadataCache`, and derives new recordsets through `with_context` and `with_company` (`src/odoo_sdk/records/recordset.py`). The package is named `odoo_sdk`, not `odoo_service`.

# Goal

## Problem

The current SDK has no first-class environment object. Context lives only inside `OdooQuery`, which means context handling is tied to one builder abstraction instead of being a property of execution state. That makes it difficult to add recordset behavior, consistent `with_context` semantics, or future session-aware features without widening unrelated classes. The SDK needs a stable root object that carries executor access and context safely.

## Solution

Introduce `OdooEnv` as the environment root for Phase A. `OdooEnv` will own executor access, current context, and environment derivation, and it will give `OdooClient` a clean way to expose context-aware behavior without losing its facade role. This creates a consistent home for context before recordsets and compatibility shims are layered on top.

# Requirements

## Functional Requirements

- A new `OdooEnv` abstraction must exist in the `odoo_service` package.
- `OdooEnv` must hold a reference to the active execution mechanism used by the SDK for model method calls.
- `OdooEnv` must hold context state as explicit environment data rather than hiding it inside `OdooQuery`.
- `OdooEnv` must support derivation or cloning with additional context such that `with_context`-style behavior creates a new environment instead of mutating the current one in place.
- `OdooEnv` must defensively copy incoming context data so later caller-side mutation does not alter stored environment state.
- `OdooEnv` must expose a consistent model-lookup path that later Phase A tasks can use to construct model-bound behavior and recordsets.
- `OdooClient` must expose a root environment in a way that preserves its role as the top-level facade.
- The implementation must define how environment context is surfaced for downstream consumers without allowing accidental shared mutable state.
- The implementation must keep `OdooEnv` as the Phase A boundary for context and environment derivation without introducing a fuller `OdooSession` abstraction in this phase.
- Local unit tests must cover environment creation, empty-context behavior, context derivation, and defensive copying.

## Non-Functional Requirements

- Context derivation must be immutable from the caller's perspective.
- The environment abstraction must not require network I/O at construction time.
- The environment must remain thin and must not absorb responsibilities that belong to recordsets, metadata caches, or future session policy layers.
- The design must stay compatible with the existing synchronous executor model.
- The design must make it clear that execution policy stays on the existing executor seam until a later phase defines a fuller session layer.

# Acceptance Criteria

- [ ] A root environment can be obtained from `OdooClient` without breaking existing client construction paths.
- [ ] Creating an environment with context and deriving a new environment with additional context leaves the original environment unchanged.
- [ ] Mutating the caller's original context dictionary after environment creation does not change the environment's stored context.
- [ ] Mutating a derived environment's context does not leak back into the parent environment.
- [ ] A model lookup path exists on `OdooEnv` and is sufficient for the Phase A recordset implementation to bind model behavior to the environment.
- [ ] Unit tests cover environment creation and context derivation behavior.

# Out of Scope

- Introducing metadata caches or field adapters.
- Adding a full `OdooSession` abstraction with retry, timeout, or error-mapping policy.
- Rich relation traversal or field-level lazy loading.
- Any async API surface.
