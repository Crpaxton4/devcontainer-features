# Feature Name

OdooModel Re-routing Through Phase A Primitives

# Goal

## Problem

`OdooModel` is part of the established public surface, but it currently owns search and browse behavior directly on top of the executor. If Phase A introduces envs, domains, and recordsets without re-routing `OdooModel`, the SDK will end up with duplicate execution paths and incompatible behavior over time. The implementation needs a way to preserve current call sites while moving the real control path underneath them to the new primitives.

## Solution

Re-implement the relevant `OdooModel` behavior on top of `OdooEnv`, `DomainExpression`, and `OdooRecordset`. `OdooModel` should remain a thin proxy and compatibility layer, preserving current call-site usability while delegating the core work to the new abstractions. This keeps the public surface stable during Phase A without allowing `OdooModel` to continue as the architectural center.

# Requirements

## Functional Requirements

- `OdooModel.search()` must continue to support current search call sites by returning the current fluent compatibility surface while routing its underlying behavior through the Phase A primitives.
- `OdooModel.browse()` must route through recordset-backed behavior while preserving its current caller-facing raw-row expectations during the Phase A transition unless a later task explicitly documents a different compatibility change.
- `OdooModel` must use the canonical domain path introduced by `DomainExpression` for search-related operations.
- `OdooModel` must use env-bound execution rather than managing query-only context state itself.
- The implementation must identify which existing `OdooModel` methods remain thin pass-through helpers in Phase A and must avoid moving new business logic into the model proxy.
- Current helpers such as `search_ids`, `exists`, `search_read`, and `search_count` must remain usable and must align with the new underlying execution path.
- Compatibility behavior that still returns raw rows must be explicit rather than accidental.
- Local regression tests must cover the preserved `OdooModel` behavior that current callers depend on.

## Non-Functional Requirements

- `OdooModel` must remain lightweight and proxy-like.
- The implementation must remove or reduce duplicated search and browse logic instead of creating two divergent long-term execution paths.
- Backward compatibility must be prioritized for current tests and common call sites.
- The refactor must stay small enough to keep later compatibility work in `OdooQuery` straightforward.

# Acceptance Criteria

- [ ] Current `OdooModel` call sites remain usable without requiring caller changes.
- [ ] `OdooModel.search()` no longer owns independent domain or context logic outside the new Phase A primitives.
- [ ] `OdooModel.search()` continues returning the current fluent compatibility surface during Phase A while delegating to the new primitives underneath.
- [ ] `OdooModel.browse()` is implemented through recordset-backed behavior but still preserves current caller-facing raw-row expectations for the Phase A transition.
- [ ] Existing helper methods on `OdooModel` continue to behave consistently after the re-route.
- [ ] Regression tests cover preserved `OdooModel` behavior and confirm that the new primitives are the controlling execution path.

# Out of Scope

- Expanding `OdooModel` with additional convenience methods.
- Making `OdooModel` the long-term home of relation handling or field adaptation.
- Removing `OdooModel` from the public API during Phase A.
- Phase B metadata or error-mapping work.
