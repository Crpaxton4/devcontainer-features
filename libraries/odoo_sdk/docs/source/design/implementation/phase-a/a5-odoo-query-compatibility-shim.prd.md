# Feature Name

OdooQuery Compatibility Shim

> **Status: never implemented (2026-07 audit).** No `OdooQuery` type exists in `src/odoo_sdk/`. The fluent builder compatibility shim described here is not part of the shipped SDK; chaining is expressed through `OdooRecordset` methods and `DomainExpression` composition instead. Its `unlink` terminal operation would also be blocked today: `unlink` raises `DeletionNotSupportedError` at the single transport guard (`src/odoo_sdk/transport/errors.py`). Retained as a record of the original Phase A plan.

# Goal

## Problem

Current SDK consumers may depend on the fluent `OdooQuery` API for chaining `limit`, `offset`, `order_by`, `with_context`, and terminal operations such as `ids`, `read`, `count`, `write`, and `unlink`. If Phase A ignores that surface, migration to a recordset-first core will introduce avoidable breakage. If Phase A keeps `OdooQuery` as the main source of search behavior, the new architecture will never actually become recordset-first. The project needs a compatibility layer, not a second architectural center.

## Solution

Keep `OdooQuery` available and behaviorally stable for existing fluent call sites, but re-route its execution through the new env, domain, and recordset primitives wherever practical. `OdooQuery` should remain immutable and builder-like, but it should clearly operate as a transitional wrapper over the new Phase A core rather than as the core itself.

# Requirements

## Functional Requirements

- `OdooQuery` must remain constructible from existing `OdooModel.search()` call sites.
- `OdooQuery` must preserve its current fluent chaining model for `search`, `limit`, `offset`, `order_by`, and `with_context`.
- Query cloning must remain immutable from the caller's perspective.
- Query execution must route domain inputs through `DomainExpression` rather than keeping separate ad hoc domain handling inside `OdooQuery`.
- Query execution must use env- or recordset-backed behavior for terminal operations where that can be done without breaking compatibility.
- The compatibility shim must preserve `ids`, `read`, `count`, `write`, and `unlink` semantics expected by current callers.
- Phase A compatibility work must not repurpose `OdooQuery` terminal operations into recordset-returning behavior unless a later task explicitly documents and tests that compatibility change.
- `with_context` must merge context predictably and must not mutate previously created query instances.
- The implementation must avoid adding new long-term responsibilities or new DSL surface area to `OdooQuery` during Phase A.
- Local tests must cover search execution, count, read, write, unlink, ordering, pagination, and context behavior.

## Non-Functional Requirements

- The compatibility layer must stay deterministic and easy to reason about.
- The refactor must reduce duplication rather than creating an independent second execution stack.
- Existing query behavior must remain stable unless the new behavior is explicitly documented as a Phase A compatibility decision.
- The code must make it clear to maintainers that `OdooQuery` is transitional.

# Acceptance Criteria

- [ ] Existing fluent `OdooQuery` call sites continue to work without caller-side changes.
- [ ] Query cloning remains immutable: updating limit, offset, order, domain, or context produces a new query object.
- [ ] Terminal query operations execute through the canonical Phase A domain and env path rather than through fully separate legacy logic.
- [ ] Compatibility tests cover `ids`, `read`, `count`, `write`, `unlink`, `order_by`, `limit`, `offset`, and `with_context` behavior.
- [ ] The implementation does not add new public `OdooQuery` features beyond what is required to preserve compatibility.

# Out of Scope

- Removing `OdooQuery` during Phase A.
- Adding a richer query DSL or ORM-style relational traversal to `OdooQuery`.
- Phase B field adaptation or metadata-aware query behavior.
- Deprecation removal or migration enforcement.
