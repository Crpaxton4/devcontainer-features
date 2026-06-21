# Feature Name

Phase B Guardrails and Semantic Growth Contract

# Goal

## Problem

Phase A establishes the recordset-first architectural direction, but it intentionally defers most of the semantics that make an Odoo SDK useful beyond transport delegation. Phase B is the first phase that adds metadata-aware behavior, relation semantics, and explicit failure handling, which means it is also the point where scope can drift quickly. Without a written contract, implementation work can split across incompatible paths: hidden behavior changes in compatibility wrappers, premature plugin work, or deeper transport refactors than the phase actually requires. The team needs a single definition of what Phase B adds, what boundaries must remain stable, and what still stays deferred.

## Solution

Define a Phase B contract that locks the semantic scope to metadata caching, field adaptation for a narrow set of field categories, x2many command helpers, explicit error mapping, and local live-Odoo validation. The contract must preserve the established public surfaces while confirming that the new behavior is routed through Phase A abstractions rather than through parallel legacy code paths. It must also explicitly defer plugin infrastructure, typed adapters, async behavior, and broader telemetry work to Phase C.

# Requirements

## Functional Requirements

- Phase B must preserve the usability of the current public entry points: `OdooClient`, `OdooModel`, `OdooQuery`, `OdooExecutor`, and `CommandDispatcher`.
- Phase B must preserve the Phase A architectural direction in which recordsets and env-bound behavior remain the primary semantic control path.
- The Phase B contract must explicitly define the supported field categories for semantic adaptation as: many2one, one2many, many2many, date, datetime, and binary.
- The Phase B contract must explicitly define metadata caching, field adaptation, x2many command serialization, and error mapping as internal growth work rather than as a new public architecture direction.
- The contract must define that compatibility surfaces continue to exist, but they must route through the same Phase B internals as recordset-first flows rather than becoming long-term parallel implementations.
- The contract must state that raw transport behavior and raw extraction paths remain explicit where Phase A already preserved them, and that any richer semantics introduced in Phase B must document how they coexist with those preserved paths.
- The contract must define the minimum success conditions for Phase B as: one stable metadata cache boundary, one shared adaptation path for in-scope field categories, one shared x2many command serialization path, one explicit mapped error taxonomy, and one local live-Odoo validation path.
- The contract must explicitly defer plugin registries, typed model adapters, async facades, broad tracing or telemetry expansion, CI pipelines, package publishing, and release automation.
- The contract must align with the architecture plan, ADRs, and design-pattern guidance already approved for the repository.

## Non-Functional Requirements

- The guardrails must be specific enough to prevent Phase B from reopening core architecture decisions already made in Phase A.
- The language must remain consistent with the repository's recordset-first vocabulary and with the existing local-tooling-only workflow.
- The contract must prefer additive internal behavior over disruptive public API churn.
- The contract must remain testable through local unit checks and local live-integration checks without requiring hosted infrastructure.

# Acceptance Criteria

- [ ] The Phase B contract explicitly lists the behavior added in this phase and limits that behavior to metadata caching, field adaptation, x2many helpers, explicit error mapping, and live-Odoo validation.
- [ ] The contract explicitly confirms that the in-scope adapted field categories are many2one, one2many, many2many, date, datetime, and binary.
- [ ] The contract explicitly states that recordset-first internals remain the primary control path for the new semantics.
- [ ] The contract explicitly states that compatibility layers must delegate to the same internals rather than growing separate behavior.
- [ ] The contract explicitly lists deferred work for plugins, typed adapters, async behavior, telemetry expansion, CI, and release automation.
- [ ] The implementation team can use the contract to evaluate every later Phase B task without reopening the core architecture direction.

# Out of Scope

- Implementing plugin registries or model-specific extension protocols.
- Designing typed model adapters or code generation.
- Introducing an async facade or broader concurrency model.
- Adding CI, hosted integration infrastructure, publishing, or release automation.
