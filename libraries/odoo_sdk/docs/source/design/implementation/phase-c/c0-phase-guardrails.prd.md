# Feature Name

Phase C Guardrails and Extensibility Contract

> **Status: never implemented (2026-07 audit).** No part of Phase C shipped. `src/odoo_sdk/` contains no plugin contract, plugin registry, typed-adapter layer, or execution-policy seam for tracing, retry, timeout, or telemetry, and there is no async facade. The `adapters/` package is unrelated to this phase: it holds `state_persistence.py` and `external_sync.py` for the task-tracker. Retained as a record of the original Phase C plan.

# Goal

## Problem

Phase C is the first point where the SDK deliberately adds long-term extension seams and operational policy hooks. Without an explicit contract, plugin work, typed adapters, tracing hooks, and async discussion can sprawl across the codebase and quietly reopen architecture decisions that Phase A and Phase B were meant to settle. That would make extension behavior inconsistent, increase coupling between compatibility layers and the recordset-first core, and blur the boundary between supported hooks and internal implementation details. The team needs one written definition of what Phase C adds, what it must preserve, and what remains intentionally deferred.

## Solution

Define a written Phase C contract before implementation expands the extensibility and operational surface area. This contract will lock the Phase C scope around narrow plugin hooks, optional typed adapters for selected stable models, execution policy hooks, and async-boundary evaluation while preserving the synchronous facade and the recordset-first architecture established earlier. The result should let the implementation team evaluate every later Phase C task against one stable architectural baseline.

# Requirements

## Functional Requirements

- Phase C must preserve the usability of the current public entry points: `OdooClient`, `OdooModel`, `OdooQuery`, `OdooExecutor`, and `CommandDispatcher`.
- Phase C must build on the Phase A and Phase B architecture rather than reopening the recordset-first direction.
- The contract must explicitly identify the Phase B prerequisites that Phase C depends on: recordset-centered internals, centralized domain handling, metadata caching, field adaptation seams, and explicit error-mapping boundaries.
- Phase C must define its in-scope additions as: narrow plugin contracts, plugin-aware internal wiring, optional typed adapters for selected stable internal models, execution policy hooks for tracing, retry, timeout, and local telemetry, and a documented async evaluation boundary.
- Phase C must define the synchronous facade as the default supported execution path throughout the phase.
- Phase C must define plugin hooks as narrow extension seams rather than arbitrary interception points across all internals.
- Phase C must define typed adapters as optional and selective rather than a replacement for the SDK's default dynamic behavior.
- Phase C must define execution policy behavior as a session- or executor-adjacent concern rather than a responsibility of `OdooClient`, `OdooModel`, or `OdooQuery`.
- Phase C must define the async outcome as an architectural decision boundary and not as an automatic commitment to ship async APIs immediately.
- The contract must explicitly document that Phase C does not include a forced migration to async, broad code generation for all Odoo models, hosted observability rollout, CI automation, package publishing automation, release automation, or a redesign of the recordset-first core.
- The contract must define the minimum success conditions for the phase: documented plugin seams, centralized plugin-aware execution, optional typed adapter support for selected stable models, centralized execution policy hooks, a documented async decision, and local-only validation.
- The contract must align with the architecture plan, ADRs, and design-pattern guidance already approved for the repository.

## Non-Functional Requirements

- The guardrails must be precise enough to prevent scope creep during extensibility work.
- The language must stay consistent with the architecture plan, ADRs, and current package naming.
- The contract must prefer additive, local-testable extension seams over framework-style machinery.
- The contract must remain local-tooling friendly and must not require CI, hosted services, or external control planes.
- The contract must make it easy for maintainers to tell which Phase C proposals belong in a later phase instead.

# Acceptance Criteria

- [ ] The Phase C contract explicitly states which existing public surfaces must remain usable throughout the phase.
- [ ] The contract identifies the Phase A and Phase B prerequisites that Phase C assumes rather than redefining them.
- [ ] The contract assigns one primary responsibility boundary each to plugin contracts, plugin-aware wiring, typed adapters, execution policy hooks, and async-boundary evaluation.
- [ ] The contract explicitly states that the synchronous facade remains the default and that async work is evaluative, not mandatory implementation scope.
- [ ] The contract explicitly lists deferred work for broad code generation, hosted observability, CI, packaging, and release automation.
- [ ] The Phase C contract can be used by the implementation team to evaluate every later Phase C task without reopening the core architecture direction.

# Out of Scope

- Implementing a full asynchronous public client during Phase C.
- Replacing the dynamic SDK model with generated typed clients.
- Introducing hosted telemetry, tracing backends, or centralized operational platforms.
- Any CI, packaging, publishing, or release automation work.
