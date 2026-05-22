# Phase A Implementation Checklist

## Objective

Implement the minimum architectural changes needed to introduce `OdooEnv`, `DomainExpression`, and `OdooRecordset` while preserving the current public entry points and keeping `OdooQuery` as a compatibility layer.

## PRD-Ready Context

### Problem statement

The current SDK has transport, model proxy, and query-builder abstractions, but it does not have a first-class environment or recordset abstraction. That makes context handling, relation handling, and future ORM-like behavior harder to extend without widening `OdooModel` and `OdooQuery` over time.

### Desired outcome

- `OdooClient` remains the top-level facade.
- Query context moves toward an environment or recordset concern.
- The SDK gains a stable record identity abstraction.
- Domain handling stops being tied to a raw list-of-tuples alias at the public boundary.
- Existing consumers can keep using current entry points during the transition.

### Non-goals

- No field metadata cache yet.
- No rich field adapters yet.
- No x2many command helpers yet.
- No plugin system yet.
- No async facade.
- No CI or release automation work.

### Constraints

- Do not break established public surfaces: `OdooClient`, `OdooModel`, `OdooQuery`, `OdooExecutor`, and `CommandDispatcher` must remain usable.
- Keep local tooling only.
- Prefer internal use of the already-selected patterns: Facade, Strategy, Proxy, Builder, Factory Method.
- Introduce Adapter only where it stays internal and does not widen public surfaces.

### Success signal

- `OdooClient` still works as the package entry point.
- `OdooModel.search()` and `browse()` can be re-routed through recordset-backed behavior without breaking existing call sites.
- A new environment abstraction exists and can carry context cleanly.
- A new domain abstraction exists and has a defined serialization boundary.
- Phase A can be validated fully with local tests and local documentation updates.

## Execution Order

1. Lock down Phase A invariants and naming.
2. Introduce `OdooEnv`.
3. Introduce `DomainExpression` and serialization rules.
4. Introduce `OdooRecordset`.
5. Route `OdooModel` behavior through the new abstractions.
6. Keep `OdooQuery` working as a compatibility shim.
7. Update exports, docs, and local validation.

## Implementation Checklist

## A0 - Phase Guardrails

Goal
- Define the contract for Phase A before refactoring starts.

Likely touch points
- `docs/implementation/phase-a/phase-a-architectural-contract.md`
- `docs/implementation/phase-a-implementation-checklist.md`
- `docs/odoo-sdk-architecture-plan.md`
- `docs/odoo-sdk-design-patterns.md`
- `src/__init__.py`
- `src/odoo_service/__init__.py`

Checklist
- [ ] Confirm the Phase A public surfaces that must remain stable.
- [ ] Confirm the names and ownership boundaries for `OdooEnv`, `DomainExpression`, and `OdooRecordset`.
- [ ] Confirm that `OdooQuery` remains transitional, not the long-term core abstraction.
- [ ] Confirm that Phase A excludes metadata caching, field adapters, and plugin work.
- [ ] Confirm that `OdooEnv` is the Phase A context boundary and that `OdooSession` is deferred.
- [ ] Confirm that preserved wrapper surfaces keep current caller-facing compatibility during Phase A while recordset-first behavior moves underneath them.
- [ ] Confirm that export decisions for `OdooEnv`, `DomainExpression`, and `OdooRecordset` are deferred to A6.

Done when
- The implementation team can evaluate A1 through A7 against the standalone Phase A contract without reopening ownership, compatibility, or deferment decisions.

## A1 - Introduce `OdooEnv`

Goal
- Add an environment abstraction that owns executor access and context state.

Why this exists
- Context currently lives inside `OdooQuery`. Phase A needs a stable home for execution context that is not tied to one query object.

Likely touch points
- New environment module under `src/odoo_service/` or the chosen Phase A package location.
- `src/odoo_service/odoo_client.py`
- `src/odoo_service/__init__.py`
- `src/__init__.py`

Checklist
- [ ] Define `OdooEnv` responsibilities: executor reference, context storage, model lookup, and cloning or derivation behavior.
- [ ] Define how `OdooClient` creates or exposes an environment without losing its facade role.
- [ ] Define how context is stored and copied so mutation does not leak across operations.
- [ ] Add local tests for environment creation and context derivation.

Done when
- An environment object exists, can carry context safely, and can be used as the context root for later recordset work.

PRD inputs captured by this item
- User-visible behavior change: context handling becomes more explicit and more consistent.
- Main technical risk: duplicating responsibilities between `OdooClient` and `OdooEnv`.

## A2 - Introduce `DomainExpression`

Goal
- Replace the raw list-of-tuples public boundary with a domain abstraction that owns normalization and serialization.

Why this exists
- The current `Domain` alias is too limited for future ORM semantics and keeps domain logic as unstructured data.

Likely touch points
- New domain module under `src/utils/` or the chosen Phase A package location.
- `src/utils/types.py`
- `src/odoo_service/odoo_model.py`
- `src/odoo_service/odoo_query.py`
- Tests that currently assume raw domain lists only.

Checklist
- [ ] Define the minimum Phase A `DomainExpression` contract.
- [ ] Define serialization from the new abstraction to the current XML-RPC-compatible domain payload.
- [ ] Decide how existing list-of-tuples inputs are accepted during transition.
- [ ] Add local tests for normalization and serialization behavior.

Done when
- Domain data has one canonical normalization path and existing callers still have a compatibility path.

PRD inputs captured by this item
- User-visible behavior change: none required initially if compatibility is preserved.
- Main technical risk: over-designing the domain object before full boolean algebra is required.

## A3 - Introduce `OdooRecordset`

Goal
- Add the new core abstraction that represents model identity, ids, environment, and record-oriented operations.

Why this exists
- Phase A needs a stable abstraction that mirrors Odoo recordset semantics more closely than raw ids or row dictionaries.

Likely touch points
- New recordset module under `src/odoo_service/` or the chosen Phase A package location.
- `src/odoo_service/odoo_model.py`
- `src/odoo_service/odoo_client.py`
- `src/odoo_service/__init__.py`
- `src/__init__.py`

Checklist
- [ ] Define the minimum `OdooRecordset` state: model name, ids, env, and any compatibility helpers needed.
- [ ] Implement the minimum recordset operations listed in the architecture plan: `read`, `write`, `unlink`, `exists`, `browse`, `search`, and `with_context`.
- [ ] Define when recordsets return raw rows versus when they return another recordset.
- [ ] Preserve immutability or copy-on-write semantics for ids and context.
- [ ] Add local tests for record identity, chaining, and context behavior.

Done when
- A recordset abstraction exists and can support Phase A model operations without requiring consumers to construct it manually.

PRD inputs captured by this item
- User-visible behavior change: the SDK gains a stable record-oriented core abstraction.
- Main technical risk: accidentally creating a second query builder instead of a real recordset abstraction.

## A4 - Re-route `OdooModel` Through Phase A Primitives

Goal
- Keep current `OdooModel` call sites working while moving internal control to `OdooEnv`, `DomainExpression`, and `OdooRecordset`.

Why this exists
- `OdooModel` is part of the current public surface and cannot be removed in Phase A.

Likely touch points
- `src/odoo_service/odoo_model.py`
- `src/odoo_service/odoo_client.py`
- New environment and recordset modules

Checklist
- [ ] Re-implement `search()` on top of the new abstractions.
- [ ] Re-implement `browse()` on top of the new abstractions.
- [ ] Decide which existing methods remain direct pass-through helpers in Phase A.
- [ ] Ensure `OdooModel` still behaves like a proxy rather than gaining more business logic.
- [ ] Add local regression tests for current `OdooModel` behavior that must remain stable.

Done when
- Existing `OdooModel` entry points still work, but their core behavior is routed through the new Phase A primitives.

PRD inputs captured by this item
- User-visible behavior change: minimal if compatibility holds.
- Main technical risk: introducing duplicate execution paths that diverge over time.

## A5 - Keep `OdooQuery` as a Compatibility Shim

Goal
- Preserve current fluent call sites while preventing `OdooQuery` from remaining the architectural center.

Why this exists
- Current users may depend on `OdooQuery`, but the long-term design should center on recordsets.

Likely touch points
- `src/odoo_service/odoo_query.py`
- `src/odoo_service/odoo_model.py`
- Local tests for chained query behavior

Checklist
- [ ] Decide which `OdooQuery` responsibilities remain valid in Phase A.
- [ ] Re-route query execution to the new Phase A abstractions where practical.
- [ ] Keep fluent chaining behavior and immutability stable.
- [ ] Avoid adding new long-term responsibilities to `OdooQuery` during this phase.
- [ ] Add local compatibility tests for search, count, read, write, unlink, order, limit, offset, and context behavior.

Done when
- Existing query-based call sites still function, and `OdooQuery` clearly behaves as a compatibility layer rather than the new core.

PRD inputs captured by this item
- User-visible behavior change: none required immediately.
- Main technical risk: allowing the compatibility shim to block the recordset-first direction.

## A6 - Update Package Exports and Documentation

Goal
- Make the new Phase A abstractions visible in the package where appropriate and document how they relate to current surfaces.

Why this exists
- Phase A adds new core concepts. Consumers and maintainers need those concepts documented before Phase B begins.

Likely touch points
- `src/__init__.py`
- `src/odoo_service/__init__.py`
- `docs/odoo-sdk-architecture-plan.md`
- `docs/odoo-sdk-design-patterns.md`
- Examples if they need clarification, but not mandatory for Phase A

Checklist
- [ ] Export new Phase A abstractions only if they are ready for public use.
- [ ] Document how `OdooEnv`, `DomainExpression`, and `OdooRecordset` relate to `OdooClient`, `OdooModel`, and `OdooQuery`.
- [ ] Document any Phase A compatibility promises and any deferred decisions.
- [ ] Keep docs aligned with the local-tooling-only workflow.

Done when
- The code and docs describe the same Phase A architecture.

## A7 - Local Validation and Release Readiness for Phase A

Goal
- Ensure Phase A is complete, locally testable, and ready for a later PRD or implementation review.

Why this exists
- Phase A is architectural. It needs a clear local validation path before Phase B starts.

Likely touch points
- `tests/test_odoo_service/`
- Existing local scripts and tooling
- Docs under `docs/`

Checklist
- [ ] Add or update unit tests for all new Phase A abstractions.
- [ ] Add or update compatibility tests for preserved surfaces.
- [ ] Define the local command path used to validate Phase A.
- [ ] Confirm that local validation does not require CI.
- [ ] Confirm that docs reflect what was actually implemented.

Done when
- A maintainer can validate Phase A end to end using only local tooling and the documented workflow.

## Exit Criteria

- [ ] `OdooClient` still acts as the main facade.
- [ ] `OdooEnv` exists and owns context cleanly.
- [ ] `DomainExpression` exists and defines the Phase A serialization boundary.
- [ ] `OdooRecordset` exists and supports the minimum Phase A record operations.
- [ ] `OdooModel.search()` and `browse()` route through the new abstractions.
- [ ] `OdooQuery` still works as a compatibility shim.
- [ ] Local tests cover both the new architecture and the preserved public surfaces.
- [ ] Phase A docs are sufficient to draft separate PRDs later without revisiting the architecture baseline.
