# Feature Name

Recordset-Centered Wiring for Phase B Semantics

> **Status: superseded (2026-07 audit).** The recordset half of this PRD holds: `OdooRecordset` is the sole owner of metadata access, field adaptation, and x2many write normalization. The compatibility half has no subject — `OdooModel` and `OdooQuery` are absent from `src/odoo_sdk/`, so there is nothing to wire or delegate. The duplication this PRD guards against cannot arise.

# Goal

## Problem

Phase B adds several new semantic behaviors, but those behaviors only improve the SDK if they are routed through one coherent internal path. If metadata caching, adaptation, x2many serialization, or error mapping are implemented independently inside `OdooModel`, `OdooQuery`, and recordset code, the repository will recreate the same split-brain architecture that Phase A was intended to remove. At the same time, compatibility surfaces still matter for current consumers and cannot simply disappear. The SDK needs one wiring plan that keeps recordsets at the center while preserving predictable legacy behavior.

## Solution

Route Phase B behavior through recordset-first internals and require `OdooModel` and `OdooQuery` compatibility methods to delegate into those same internals. Metadata retrieval, field adaptation, x2many normalization, and error mapping must each have one owning boundary, and compatibility methods must be wrappers over those boundaries rather than alternative implementations. This preserves current call sites while preventing semantic drift between new and legacy paths.

# Requirements

## Functional Requirements

- Recordset-first internals must remain the primary owner of Phase B semantic behavior for metadata access, adapted field handling, and x2many write normalization.
- `OdooModel` methods that overlap with recordset behavior must delegate to the same Phase B internals rather than duplicating metadata, adaptation, or serialization logic.
- `OdooQuery` terminal operations must continue to function, but they must delegate into the same recordset-centered execution path rather than becoming owners of Phase B semantics.
- The implementation must define explicitly which operations preserve Phase A raw extraction behavior and which operations expose Phase B semantic behavior.
- Metadata lookups needed by compatibility paths must use the same shared metadata cache as recordset flows.
- Any adapted read behavior exposed through compatibility paths must use the same field-adaptation layer as recordset flows.
- Any x2many write behavior exposed through compatibility paths must use the same command normalization and serialization boundary as recordset flows.
- Error mapping observed from compatibility paths must come from the same shared mapping boundary as recordset flows.
- The wiring plan must avoid duplicate `fields_get` retrieval logic, duplicate value-adaptation logic, and duplicate x2many serialization logic across the codebase.
- Local regression tests must cover current public entry points that now rely on Phase B internals, including at minimum representative `OdooModel` and `OdooQuery` paths.

## Non-Functional Requirements

- Internal ownership boundaries must stay clear enough that later plugin work can extend them without another architectural reset.
- Compatibility wrappers must remain thin and maintainable.
- The implementation must preserve predictable behavior for current callers while still moving the semantic center of gravity toward recordsets.
- The wiring plan must prefer reuse over convenience branches, even if some compatibility methods become simple delegators.

# Acceptance Criteria

- [ ] Recordset-first internals are the primary control path for metadata, adaptation, x2many serialization, and mapped errors.
- [ ] `OdooModel` compatibility methods delegate to the same Phase B internals rather than implementing separate logic.
- [ ] `OdooQuery` compatibility methods delegate to the same Phase B internals rather than implementing separate logic.
- [ ] Raw extraction versus adapted behavior is explicitly documented and test-covered.
- [ ] Regression tests confirm that preserved public surfaces still behave predictably after the Phase B routing changes.
- [ ] No duplicate metadata or adaptation code path remains as an unowned side path.

# Out of Scope

- Removing `OdooModel` or `OdooQuery` from the public surface.
- Designing a new top-level facade.
- Introducing plugin extension points for adapters or serializers.
- Rewriting unrelated Phase A abstractions.
