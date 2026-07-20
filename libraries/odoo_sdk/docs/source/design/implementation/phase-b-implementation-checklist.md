# Phase B Implementation Checklist

> **Status: partially superseded (2026-07 audit).** Every Phase B deliverable shipped, but the compatibility-wiring items (B5) have no subject: `OdooModel` and `OdooQuery` are absent from `src/odoo_sdk/`. The package is named `odoo_sdk`, not `odoo_service`, and validation now runs in GitHub Actions rather than local tooling only.

## Objective

Implement the minimum growth-phase changes needed to make recordsets useful beyond identity and transport delegation by adding metadata caching, field adaptation, x2many command helpers, explicit error mapping, and local integration checks.

## PRD-Ready Context

### Problem statement

Phase A establishes the environment, domain, and recordset foundations, but Phase A alone still leaves the SDK too close to the XML-RPC wire format. Model data remains under-adapted, metadata lookups would become repetitive if used broadly, and consumers would still need to understand Odoo-specific response and command shapes directly.

### Desired outcome

- Model and record operations can use cached field metadata instead of repeatedly treating all payloads as raw dictionaries.
- Common Odoo wire formats such as many2one values, x2many payloads, and date-like fields have a defined adaptation path.
- Write-side x2many operations become easier to express through SDK helpers.
- Server and transport failures are mapped into explicit SDK errors instead of remaining generic runtime failures.
- A local integration check path validates behavior against a live Odoo instance.

### Non-goals

- No plugin registry yet.
- No typed model adapters yet.
- No async facade.
- No tracing or telemetry expansion beyond what is strictly needed to support local validation.
- No CI, package publishing, or release automation work.

### Constraints

- Preserve established public surfaces: `OdooClient`, `OdooModel`, `OdooQuery`, `OdooExecutor`, and `CommandDispatcher` must remain usable.
- Build on the Phase A abstractions instead of reopening the core architecture.
- Keep tooling local-only.
- Prefer the already-selected patterns that apply directly here: Adapter for field and value translation, Strategy for execution seams, Decorator only if a repeated cross-cutting execution concern appears.
- Keep compatibility behavior explicit so Phase B does not turn legacy surfaces into hidden forks.

### Success signal

- Metadata access has a defined cache boundary.
- Read-side data adaptation exists for the Phase B field categories in scope.
- x2many write helpers exist and serialize correctly.
- Explicit SDK error classes exist and are used consistently for mapped failures.
- Local integration checks validate the SDK against at least one live Odoo instance.

## Execution Order

1. Lock down Phase B boundaries and extension seams.
2. Introduce metadata cache behavior around `fields_get`.
3. Introduce field adaptation rules for Phase B field categories.
4. Introduce x2many command helpers.
5. Introduce explicit error taxonomy and mapping.
6. Wire Phase B behavior through recordsets and compatibility layers.
7. Add local integration checks, docs, and exit validation.

## Implementation Checklist

## B0 - Phase Guardrails

Goal
- Define the exact Phase B contract before implementation expands the data model and error model.

Likely touch points
- `docs/implementation/phase-b/phase-b-semantic-growth-contract.md`
- `docs/implementation/phase-b-implementation-checklist.md`
- `docs/implementation/phase-a/phase-a-architectural-contract.md`
- `docs/odoo-sdk-architecture-plan.md`
- `docs/odoo-sdk-design-patterns.md`
- `docs/architecture/ADR-003-metadata-cache-and-plugin-adapters.md`

Checklist
- [ ] Confirm the preserved public surfaces and the Phase A baseline that remain in force throughout Phase B.
- [ ] Confirm the exact Phase B field categories in scope: many2one, one2many, many2many, date, datetime, and binary.
- [ ] Confirm that metadata caching, field adaptation, x2many helpers, and mapped errors are internal semantic growth work rather than a new public architecture direction.
- [ ] Confirm that raw extraction remains explicit and that richer semantics must document coexistence rather than silently replacing preserved raw paths.
- [ ] Confirm that compatibility layers delegate to the same Phase B internals used by recordset-first flows.
- [ ] Confirm that plugin work, typed adapters, async work, telemetry expansion, CI, publishing, and release automation remain deferred.

Done when
- The implementation team can evaluate B1 through B7 against the standalone Phase B contract without reopening recordset-first direction, compatibility routing, or deferment decisions.

## B1 - Introduce Metadata Cache

Goal
- Add a cache boundary for `fields_get` results so field-aware behavior can scale locally without repeated metadata round trips.

Why this exists
- Phase B needs metadata-driven behavior. Without a cache, field adaptation would either be expensive or inconsistently applied.

Likely touch points
- New metadata cache module under the chosen package location.
- `src/odoo_service/odoo_model.py`
- Phase A recordset and environment modules
- Tests covering `fields_get` behavior

Checklist
- [ ] Define cache ownership and lifecycle: recordset, environment, session, or dedicated metadata service.
- [ ] Define cache key strategy for model name, requested field set,
  requested attribute set, and any runtime-scoped context that changes raw
  `fields_get` payloads.
- [ ] Define miss behavior and fallback behavior when metadata retrieval fails.
- [ ] Define whether cache invalidation is manual, per-process, or intentionally deferred for Phase B.
- [ ] Add local tests for cache hits, misses, and repeated metadata access.

Done when
- Field metadata has one stable retrieval path and repeated lookups can reuse cached results safely within the local runtime model.

PRD inputs captured by this item
- User-visible behavior change: improved consistency and lower repeated metadata overhead.
- Main technical risk: introducing cache semantics that are too rigid before real invalidation needs are understood.

## B2 - Introduce Field Adaptation

Goal
- Add internal adapters that translate selected Odoo wire formats into predictable SDK-facing values.

Why this exists
- Recordsets are only partly useful if consumers still need to decode tuples, date strings, and relation payloads manually.

Likely touch points
- New field or value adapter modules under the chosen package location.
- `src/odoo_service/field_adapters.py`
- `src/odoo_service/field_values.py`
- Metadata cache module
- Phase A recordset implementation
- `src/odoo_service/odoo_model.py`
- Local tests for read behavior

Checklist
- [ ] Define the adaptation rules for many2one values.
- [ ] Define the adaptation rules for one2many and many2many read-side values.
- [ ] Define normalization rules for date and datetime fields.
- [ ] Define normalization rules for binary fields for Phase B.
- [ ] Define where adaptation is triggered: recordset-owned materialization via `read_adapted()` and `search_read_adapted()`, with thin compatibility delegates layered above it.
- [ ] Ensure adaptation can be applied without breaking callers that still rely on compatibility surfaces.
- [ ] Add local tests for each supported Phase B field category.

Done when
- The Phase B field categories have a consistent internal adaptation path and local tests prove the behavior.

PRD inputs captured by this item
- User-visible behavior change: common Odoo field types become easier to consume.
- Main technical risk: leaking half-adapted data across different read paths.

## B3 - Introduce x2many Command Helpers

Goal
- Add helper constructs for write-side x2many operations so consumers do not have to build raw Odoo command tuples directly.

Why this exists
- x2many write semantics are Odoo-specific and cumbersome. Phase B should reduce that protocol exposure.

Likely touch points
- New helper module for x2many commands
- Recordset write path
- `src/odoo_service/__init__.py`
- `src/odoo_service/odoo_model.py`
- `src/odoo_service/odoo_query.py`
- Local tests for write serialization

Checklist
- [x] Define the minimum helper API needed for common x2many operations in Phase B.
- [x] Define serialization from helper objects to Odoo command tuple payloads.
- [x] Define whether raw tuple input remains accepted for compatibility and how both paths coexist.
- [x] Add local tests for command helper serialization and write-path usage.

Done when
- Consumers have an SDK-supported way to express x2many write commands without constructing raw tuples manually.

PRD inputs captured by this item
- User-visible behavior change: x2many writes become more ergonomic.
- Main technical risk: designing a helper API that is too broad before common usage patterns are confirmed.

## B4 - Introduce Explicit Error Taxonomy and Mapping

Goal
- Replace generic runtime failures with explicit SDK errors for auth, access, validation, missing records, and transport faults.

Why this exists
- Phase B adds more semantics to the SDK. Error handling needs to become equally structured so consumers can respond predictably.

Likely touch points
- New errors module
- `src/odoo_service/odoo_rpc_executor.py`
- Phase A environment or session abstraction if present
- Local tests for transport and server failures

Checklist
- [ ] Define the minimum Phase B error classes.
- [ ] Define how XML-RPC faults and local execution failures map into those classes.
- [ ] Define where error mapping happens so the logic is not duplicated across model and query layers.
- [ ] Ensure compatibility behavior is documented for callers that currently expect generic runtime exceptions.
- [ ] Add local tests for each mapped error category.

Done when
- Failure modes are explicit, testable, and mapped consistently through one execution path.

PRD inputs captured by this item
- User-visible behavior change: failure handling becomes more precise.
- Main technical risk: creating an error hierarchy that is too detailed for actual consumer needs.

## B5 - Wire Phase B Behavior Through Recordsets and Compatibility Layers

Goal
- Route caching, adaptation, x2many helpers, and error mapping through the recordset-first architecture while preserving compatibility for `OdooModel` and `OdooQuery`.

Why this exists
- Phase B should strengthen the Phase A architecture, not create side paths that bypass it.

Likely touch points
- Phase A recordset, environment, and domain modules
- `src/odoo_service/odoo_model.py`
- `src/odoo_service/odoo_query.py`
- New cache, adapter, command-helper, and error modules

Checklist
- [ ] Decide which read and write paths become recordset-centered in Phase B. Raw `read()` and `search_read()` stay explicit extraction paths; adapted semantics flow through `read_adapted()` and `search_read_adapted()`.
- [ ] Ensure `OdooModel` compatibility helpers use the same Phase B internals as recordsets.
- [ ] Ensure `OdooQuery` compatibility behavior still works without becoming the owner of adaptation or cache semantics.
- [ ] Remove or avoid duplicate metadata or adaptation code paths.
- [ ] Add local regression tests for current public surfaces that now pass through Phase B logic.

Done when
- Phase B behavior is applied through one coherent set of internals and compatibility surfaces still behave predictably.

PRD inputs captured by this item
- User-visible behavior change: compatibility surfaces gain richer semantics without requiring immediate migration.
- Main technical risk: divergence between recordset behavior and legacy helper behavior.

## B6 - Add Local Integration Checks Against Live Odoo

Goal
- Validate the Phase B semantics locally against a real Odoo instance.

Why this exists
- Mock-only validation is not enough once metadata-driven adaptation and x2many command semantics are introduced.

Likely touch points
- Manual smoke examples under `examples/`
- Example flows that already access live Odoo
- Docs under `docs/`

Checklist
- [ ] Define the minimum live-Odoo scenarios needed to validate Phase B.
- [ ] Cover metadata retrieval, at least one adapted relation field, at least one normalized date or datetime field, x2many serialization, and mapped error behavior where practical.
- [ ] Document the local setup assumptions for running the checks.
- [ ] Document how the manual smoke path uses live metadata to decide whether `date_deadline` should round-trip as a Python `date` or `datetime`.
- [ ] Keep the validation path local-only and scriptable.

Current manual command path
- `python examples/live_phase_b_smoke_test.py --allow-live-production`
- The smoke example should follow live `fields_get` metadata for `project.task.date_deadline`, so it can validate either `date` or `datetime` behavior without hard-coding one schema ahead of time.
- Keep the live check in `examples/`; `tests/` remains reserved for automated validation.

Done when
- A maintainer can run a local live-Odoo validation path that exercises the key new semantics in Phase B.

PRD inputs captured by this item
- User-visible behavior change: none directly, but confidence in Phase B semantics increases.
- Main technical risk: building a validation path that is too environment-specific to be maintainable.

## B7 - Update Documentation and Local Validation Workflow

Goal
- Ensure docs, local workflow, and Phase B implementation remain aligned.

Why this exists
- Phase B introduces semantics that consumers will need explained clearly before Phase C expands extensibility further.

Likely touch points
- `docs/odoo-sdk-architecture-plan.md`
- `docs/odoo-sdk-design-patterns.md`
- `docs/implementation/phase-b-implementation-checklist.md`
- Examples if they need clarification

Checklist
- [ ] Document the Phase B semantics added around metadata caching, field adaptation, x2many helpers, and error mapping.
- [ ] Keep the pattern guidance aligned with actual implementation choices.
- [ ] Document the local validation path used for Phase B.
- [ ] Confirm docs still reflect the local-tooling-only workflow.
- [ ] Keep the manual live validation path documented as an example script rather than automated test or task wiring.

Done when
- The code, docs, and validation workflow all describe the same Phase B behavior.

## Exit Criteria

- [ ] A metadata cache exists and has a documented ownership boundary.
- [ ] The Phase B field categories have a consistent adaptation path.
- [ ] x2many command helpers exist and serialize correctly.
- [ ] Explicit SDK error classes exist and are mapped consistently.
- [ ] Recordset-first internals remain the main control path for the new Phase B behavior.
- [ ] `OdooModel` and `OdooQuery` compatibility behavior still works.
- [ ] Local integration checks exercise the key new Phase B semantics against a live Odoo instance.
- [ ] Phase B docs are sufficient to draft separate PRDs later without revisiting the implementation baseline.

## Detailed PRDs

```{toctree}
:maxdepth: 1
:glob:

phase-b/*
```
