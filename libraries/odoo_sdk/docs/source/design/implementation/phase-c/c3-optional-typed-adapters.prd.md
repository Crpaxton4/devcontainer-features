# Feature Name

Optional Typed Adapters for Stable Internal Models

> **Status: never implemented (2026-07 audit).** No part of Phase C shipped. `src/odoo_sdk/` contains no plugin contract, plugin registry, typed-adapter layer, or execution-policy seam for tracing, retry, timeout, or telemetry, and there is no async facade. The `adapters/` package is unrelated to this phase: it holds `state_persistence.py` and `external_sync.py` for the task-tracker. Retained as a record of the original Phase C plan. The later Phase G type system, which would have subsumed this, also never shipped.

# Goal

## Problem

The SDK's default dynamic behavior is appropriate for Odoo's flexible model system, but some internal or highly stable model families benefit from stronger typing and better editor support. If the project ignores that need entirely, consumers with stable internal models will either keep writing local wrappers or treat raw dictionaries as de facto typed contracts without any shared enforcement. If the project overcorrects, it risks turning the SDK into a code-generated or typed-only system that conflicts with Odoo's dynamic nature. Phase C needs a selective typed-adapter story, not a second architecture.

## Solution

Introduce optional typed adapters for a small set of stable internal models and define how they coexist with the default dynamic model behavior. Typed adapters should be opt-in, registry-compatible, and limited to models that meet documented stability criteria. This gives selected consumers stronger typing without forcing the full SDK into a generated or static model strategy.

# Requirements

## Functional Requirements

- The implementation must define the eligibility criteria for models that can receive typed adapters in Phase C.
- Typed adapters must remain optional and must not replace the SDK's default dynamic behavior for models without an adapter.
- The implementation must define how typed adapters are registered, selected, or discovered using the Phase C extension model.
- The implementation must define which SDK operations may return typed-adapter outputs and which operations remain dynamically shaped.
- Typed adapters must coexist with recordset-first behavior rather than replacing recordsets with a separate typed client architecture.
- The implementation must define fallback behavior when no typed adapter exists for a model or when an adapter cannot be applied.
- The design must preserve compatibility for callers that still expect dynamic values or raw compatibility-layer behavior.
- The documentation must define how typed adapters relate to metadata-driven field adaptation introduced in Phase B.
- The implementation must include at least one concrete, stable-model example that demonstrates adapter selection and typed output behavior.
- Local tests must cover adapter registration, selection, typed output behavior, and fallback to the dynamic default path.

## Non-Functional Requirements

- Typed adapters must remain additive and selective.
- The feature must not require code generation across the entire Odoo model surface.
- The design must keep runtime behavior understandable for callers who do not opt into typed adapters.
- The implementation must preserve local testability and avoid introducing a separate build-time schema pipeline.
- The typed-adapter model must not create semantic drift between typed and dynamic paths for the same underlying operation.

# Acceptance Criteria

- [ ] The docs define which models qualify for typed adapters and why.
- [ ] A model without a typed adapter still follows the existing dynamic behavior without regression.
- [ ] A selected stable model can use a typed adapter through the documented registration and selection path.
- [ ] Tests cover adapter registration, typed output behavior, and fallback to the dynamic path.
- [ ] The implementation does not require broad model generation or a typed-only architecture.

# Out of Scope

- Generating typed adapters for all Odoo models.
- Replacing recordsets or compatibility layers with model-specific generated clients.
- Requiring consumers to adopt static typing to use the SDK.
- Introducing a build-time schema compiler or remote schema service.
