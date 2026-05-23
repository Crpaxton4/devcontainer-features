# Phase B Semantic Growth Contract

## Purpose

This contract is the implementation baseline for Phase B.

Its job is to let the SDK add the minimum metadata-aware and error-aware semantics needed after Phase A without reopening the recordset-first direction, creating hidden compatibility forks, or pulling extensibility work forward from Phase C. Every later Phase B task should be evaluated against this document before it is accepted.

## Preserved Public Surfaces

The following public surfaces must remain usable throughout Phase B:

| Surface | Phase B status | Guardrail |
|---|---|---|
| `OdooClient` | Preserved | Remains the top-level facade and package story |
| `OdooModel` | Preserved | Remains usable as a proxy and compatibility wrapper |
| `OdooQuery` | Preserved | Remains usable as a transitional fluent compatibility shim |
| `OdooExecutor` | Preserved | Remains the execution seam for transport behavior |
| `CommandDispatcher` | Preserved | Remains usable as an integration helper, not the ORM center |

Phase B must not remove these entry points, force callers onto replacement APIs, or turn semantic growth work into a second public architecture.

## Phase A Baseline That Remains In Force

Phase B builds on the Phase A architectural contract rather than replacing it.

- The architecture remains recordset-first.
- `OdooEnv` remains the owner of execution context and environment derivation.
- `DomainExpression` remains the canonical domain normalization and serialization path.
- `OdooRecordset` remains the identity-bearing core for env-bound record operations.
- `OdooModel` and `OdooQuery` remain compatibility layers, not the long-term center of ORM behavior.
- Raw `read()` extraction remains an explicit preserved path.
- Validation remains local-tooling only.

If a proposed Phase B change conflicts with those decisions, the change should be revised before implementation proceeds.

## Responsibility Boundaries

Phase B adds semantic growth work through shared internal boundaries rather than through new top-level APIs.

| Boundary | Owns | Does not own |
|---|---|---|
| Recordset-first semantic control path | Routing semantic behavior through env-bound and recordset-bound internals | Parallel long-term logic inside `OdooModel` or `OdooQuery` |
| Shared metadata boundary | `fields_get` retrieval normalization, cache reuse, and explicit invalidation behavior | Record payload caching, plugin-defined metadata providers, cross-process invalidation |
| Shared adaptation boundary | Read-side adaptation for `many2one`, `one2many`, `many2many`, `date`, `datetime`, and `binary` fields | Typed model adapters, model-specific plugin behavior, silent replacement of raw extraction |
| Shared x2many serialization boundary | SDK-supported helper serialization and compatibility handling for raw tuple inputs | Broad write-side DSL growth or model-specific extension protocols |
| Shared mapped-error boundary | Explicit mapping for auth, access, validation, missing-record, transport, and fallback server failures | Retry policy, tracing, timeout policy, or broader execution telemetry |
| Compatibility layers | Delegating preserved caller-facing flows into the same Phase B internals as recordset-first behavior | Owning cache semantics, adaptation rules, x2many serialization rules, or error-mapping policy |

## Resolved Phase B Decisions

### Semantic Scope

Phase B is limited to the following behavior additions:

- Metadata caching for `fields_get`-driven semantics.
- Field adaptation for these in-scope categories only: `many2one`, `one2many`, `many2many`, `date`, `datetime`, and `binary`.
- x2many command helpers and serialization support.
- Explicit SDK error taxonomy and mapped failure behavior.
- A local live-Odoo validation path for the new semantics.

The implemented x2many helper boundary is intentionally small:

- `X2ManyCommand` supports `create`, `update`, `delete`, `unlink`, `link`, `clear`, and `set` operations.
- The shared x2many boundary accepts a single helper object, a single raw tuple, or an ordered mixed list of both for `one2many` and `many2many` fields.
- Helper and raw tuple inputs are canonicalized to standard Odoo command tuples before XML-RPC execution.

These additions are internal semantic growth work. They do not define a new public architecture direction.

### Raw And Adapted Paths

- Phase A preserved raw transport behavior and raw `read()` extraction behavior, and Phase B must keep those paths explicit.
- The implemented Phase B read-side contract keeps `read()` and `search_read()` raw, while routing richer semantics through explicit adapted entry points such as `read_adapted()` and `search_read_adapted()`.
- Any richer semantic behavior introduced in Phase B must document how it coexists with raw extraction rather than silently replacing it.
- Adapted behavior must be routed through one shared internal path for the in-scope field categories.
- Ordinary scalar values remain valid in the same write payload as x2many helper values; only metadata-confirmed `one2many` and `many2many` fields are normalized through the shared x2many boundary.
- x2many command normalization must preserve caller ordering for mixed helper and raw tuple command lists.

### Compatibility Routing

- `OdooModel` and `OdooQuery` remain available during Phase B.
- Compatibility surfaces must delegate to the same cache, adaptation, x2many serialization, and mapped-error internals used by recordset-first flows.
- Phase B must not let preserved wrapper surfaces become long-term parallel implementations.

### Error Mapping Boundary

- Phase B must provide one explicit mapped-error taxonomy.
- Mapped error behavior must be observed consistently from recordset flows and compatibility flows.
- Phase B does not require a broader transport redesign or a full session-policy implementation to satisfy this boundary.

### Public Export Scope

- Phase B does not widen the supported top-level public exports beyond the preserved surfaces.
- Metadata caches, adapters, adapted relation value objects, x2many helpers, and error-mapping support may exist as internal modules without becoming new supported top-level entry points in this phase.
- x2many helpers may be re-exported from `odoo_sdk.odoo_service`, but Phase B still does not widen the package-root `odoo_sdk` export surface.

## Explicitly Deferred Work

The following work is outside Phase B and must not be smuggled in through semantic adapters, cache layers, or compatibility helpers:

- Plugin registries or model-specific extension protocols
- Typed model adapters or code generation
- Async facades or broader concurrency models
- Broad tracing, observability, or telemetry expansion
- Retry policy, timeout policy, or wider execution-policy infrastructure
- CI automation
- Hosted integration infrastructure
- Package publishing automation
- Release automation

The following work is also deferred because it would reopen responsibilities that Phase B is only strengthening, not expanding:

- Cross-process or server-driven metadata invalidation
- Plugin-owned metadata or adaptation providers
- A redesign of the recordset-first core or preserved compatibility surfaces

## Minimum Success Conditions

Phase B is successful only if all of the following are true:

1. `OdooClient` still works as the top-level facade.
2. Recordset-first internals remain the primary control path for new semantic behavior.
3. One stable metadata cache boundary exists for field-aware behavior.
4. One shared adaptation path exists for the in-scope field categories.
5. One shared x2many command serialization path exists.
6. One explicit mapped error taxonomy exists and is applied consistently.
7. Raw extraction behavior remains explicit where Phase A preserved it.
8. Compatibility surfaces remain usable while delegating into the same Phase B internals.
9. A local live-Odoo validation path exists for the key new semantics.
10. Validation remains local-tooling only.

## Evaluation Rules For Later Phase B Tasks

Every later Phase B task must be rejected or revised if it does any of the following:

- Reopens whether the architecture is recordset-first.
- Lets `OdooModel` or `OdooQuery` grow as independent semantic control paths.
- Replaces explicit raw extraction with hidden adaptation.
- Introduces a second metadata retrieval or adaptation path outside the shared internal boundary.
- Introduces x2many helpers without routing them through one shared serialization path.
- Adds plugin infrastructure, typed adapter infrastructure, async APIs, or broad telemetry hooks under Phase B labels.
- Requires CI, hosted services, publishing workflows, or release automation to declare Phase B complete.
- Forces a larger transport or session redesign than the mapped-error boundary actually requires.

Every later Phase B task should be able to answer these questions directly:

1. Which preserved surface stays usable after this task?
2. Which shared Phase B boundary owns the new behavior?
3. Does the task keep semantic routing on the recordset-first path?
4. How does the task preserve explicit raw behavior where Phase A already preserved it?
5. Does the task avoid widening Phase B with work deferred to Phase C or later?

## Traceability To Phase B Tasks

| Contract area | Primary follow-on tasks |
|---|---|
| Guardrail and scope lock | B0 |
| Shared metadata cache boundary | B1 |
| Shared field adaptation boundary | B2 |
| Shared x2many serialization boundary | B3 |
| Shared mapped-error boundary | B4 |
| Recordset-first routing and compatibility delegation | B5 |
| Local live-Odoo validation path | B6 |
| Documentation and validation workflow alignment | B7 |

## Alignment Notes

This contract is intentionally narrower than the longer-term architecture plan.

- The architecture plan describes the target trajectory across multiple phases.
- This contract describes the decisions that must hold during Phase B implementation.
- Phase C remains the place for plugin hooks, optional typed adapters, execution-policy expansion, and async-boundary evaluation.
- If a later Phase B task appears to conflict with this document, the task PRD or checklist should be revised before implementation proceeds.