# Phase C Extensibility Contract

## Purpose

This contract is the implementation baseline for Phase C.

Its job is to let the SDK add deliberate extension seams and operational policy
hooks without reopening the recordset-first direction established in Phase A and
reinforced in Phase B. Every later Phase C task should be evaluated against
this document before it is accepted.

## Preserved Public Surfaces

The following public surfaces must remain usable throughout Phase C:

| Surface | Phase C status | Guardrail |
|---|---|---|
| `OdooClient` | Preserved | Remains the main synchronous facade and package story |
| `OdooEnv` | Preserved | Remains the env and context root for recordset-first behavior |
| `DomainExpression` | Preserved | Remains the canonical domain normalization and serialization path |
| `OdooRecordset` | Preserved | Remains the identity-bearing recordset-first core |
| `OdooModel` | Preserved | Remains usable as a compatibility wrapper over the shared core |
| `OdooQuery` | Preserved | Remains usable as a compatibility builder shim over the shared core |
| `OdooExecutor` | Preserved | Remains the execution seam for transport behavior and later policy wrapping |
| `CommandDispatcher` | Preserved | Remains usable as an integration helper outside the ORM control path |

Phase C must not remove these entry points, force callers onto a replacement
architecture, or let plugin, adapter, policy, or async work create a second
public center of gravity beside the recordset-first core.

## Phase A And Phase B Baseline That Remains In Force

Phase C builds on the Phase A architectural contract and the Phase B semantic
growth contract rather than replacing them.

- The architecture remains recordset-first as described in ADR-001.
- `OdooClient` remains a thin facade rather than a policy or plugin host.
- `OdooEnv` remains the owner of execution context, environment derivation, and
  model-bound lookup.
- `DomainExpression` remains the canonical domain normalization and
  serialization path.
- `OdooRecordset` remains the identity-bearing core for shared ORM behavior.
- `OdooModel` and `OdooQuery` remain compatibility layers, not the long-term
  architectural center.
- Metadata caching remains the shared `fields_get` boundary for semantic
  behavior.
- Field adaptation remains a shared metadata-driven seam rather than a
  model-specific plugin default.
- Explicit error mapping remains an executor or session-adjacent boundary, not a
  compatibility-layer concern.
- Explicit raw extraction paths such as `read()` and `search_read()` remain
  preserved where Phase A and Phase B already preserved them.
- Validation remains local-tooling friendly.

If a proposed Phase C change conflicts with those prerequisites, the change
should be revised before implementation proceeds.

## Responsibility Boundaries

Phase C adds extensibility and operational concerns through narrow internal
boundaries rather than by widening the facade or compatibility API surface.

| Boundary | Owns | Does not own |
|---|---|---|
| Plugin contracts | Supported hook categories, typed inputs and outputs, failure behavior, deterministic rejection of invalid plugins, and explicit allowed and prohibited extension zones | Runtime ordering policy, transport replacement, arbitrary `OdooEnv` mutation, or recordset identity ownership |
| Plugin-aware internal wiring | Centralized registration, discovery, ordering, diagnostics, and one canonical plugin-aware path shared by recordset-first and compatibility surfaces | Parallel plugin stacks inside `OdooModel`, `OdooQuery`, adapters, or helper modules |
| Optional typed adapters | Selective opt-in typed projections for documented stable models, adapter selection rules, and fallback to the default dynamic path | Broad code generation, typed-only model access, or replacement of recordsets with a separate client architecture |
| Execution policy boundary | Session or executor-adjacent tracing, retry, timeout, local telemetry, policy composition, and local diagnostics | Cross-cutting behavior pushed into `OdooClient`, `OdooModel`, `OdooQuery`, or recordset methods |
| Async boundary evaluation | Criteria for future async justification, definition of what behavior stays shared versus separate, and one explicit Phase C decision outcome | Shipping a public async facade now, mixing sync and async methods on the same facade, or forcing migration away from the synchronous API |

## Resolved Phase C Decisions

### Phase C Scope

Phase C is limited to the following in-scope additions:

- Narrow plugin contracts.
- Plugin-aware internal wiring.
- Optional typed adapters for selected stable internal models.
- Execution policy hooks for tracing, retry, timeout, and local telemetry.
- A documented async evaluation boundary and decision.

These additions must remain additive to the existing synchronous,
recordset-first architecture.

### Plugin Boundary

- Plugin hooks are narrow extension seams, not arbitrary interception points.
- Allowed hook zones are limited to model-specific adaptation, selection, or
  serialization seams that already fit the Phase A and Phase B architecture.
- Unsupported zones include unrestricted transport replacement, arbitrary
  mutation of `OdooEnv`, domain serialization ownership, or direct takeover of
  recordset identity behavior.
- Plugin contracts must be explicit enough that unsupported or mis-typed
  plugins can be rejected during registration or validation.
- Plugin failures must fail fast or surface predictably; they must not silently
  corrupt execution flow or fall back unpredictably.
- Each supported hook category must state whether it is pre-operation,
  post-operation, transformation-oriented, or selection-oriented.

### Centralized Wiring

- Plugin-aware behavior must route through one coherent internal path anchored
  to the recordset-first core and shared by `OdooModel` and `OdooQuery`.
- Registration, discovery, ordering, and precedence must be deterministic for a
  given local runtime state.
- The effective plugin registration state must be inspectable for local
  diagnostics and tests.
- No-plugin, one-plugin, and multiple-plugin scenarios must be defined
  explicitly per hook category.
- The same plugin behavior must not be applied twice when a call passes through
  layered abstractions.
- Phase B caches, field adapters, and mapped errors remain shared architectural
  boundaries rather than plugin-owned systems.

### Typed Adapters

- Typed adapters are optional and selective.
- Eligibility requires a documented stable model family with clear invariants
  and a concrete payoff from stronger local typing.
- Models without a typed adapter continue to use existing dynamic behavior with
  no regression.
- Typed adapters may integrate with the Phase C extension model, but they do
  not replace `OdooRecordset`, compatibility layers, or metadata-driven field
  adaptation.
- Fallback to the dynamic default path is part of the supported behavior, not an
  error case.

### Execution Policy Boundary

- Tracing, retry, timeout, and local telemetry are session or executor-adjacent
  concerns as described by ADR-002.
- Execution policy behavior must wrap shared execution behavior rather than
  adding transport-specific branching inside facade, model, query, or recordset
  methods.
- Policy hooks must compose predictably and preserve the explicit mapped-error
  boundary introduced in Phase B.
- Local telemetry and diagnostics must remain local-tooling friendly and must
  not assume hosted observability infrastructure.

### Async Boundary

- The synchronous facade remains the default supported execution path throughout
  Phase C.
- Phase C must record one explicit async outcome: defer async work, prototype it
  in a later dedicated phase, or approve future implementation planning.
- Whatever outcome Phase C records, async remains an architectural decision
  boundary in this phase rather than an automatic commitment to ship public
  async APIs immediately.
- Any future async path must share domain normalization, record identity,
  metadata-aware adaptation rules, and error semantics while keeping transport
  implementation, coroutine-returning APIs, and lifecycle management separate.

## Explicitly Deferred Work

The following work is outside Phase C and must not be smuggled in through
plugin, adapter, policy, or documentation changes:

- Forced migration from the synchronous facade to a public async API.
- Broad code generation or typed clients for all Odoo models.
- Hosted observability, tracing backends, or centralized operational platforms.
- CI automation as a Phase C exit gate.
- Package publishing automation.
- Release automation.
- Remote plugin registries, hosted plugin distribution, or per-request plugin
  downloads.
- A redesign of the recordset-first core or removal of preserved compatibility
  surfaces.

## Minimum Success Conditions

Phase C is successful only if all of the following are true:

1. Documented plugin seams exist and clearly distinguish allowed and prohibited
   extension points.
2. Plugin-aware execution is centralized and shared by recordset-first and
   compatibility surfaces.
3. Optional typed adapter support exists for selected stable models without
   regressing the default dynamic path.
4. One canonical execution policy boundary exists for tracing, retry, timeout,
   and local telemetry.
5. Phase C records a documented async decision while keeping the synchronous
   facade as the default supported path.
6. Validation remains local-only and covers plugin contracts, plugin-aware
   behavior, typed adapters, execution policy hooks, and the async decision
   artifacts.

## Evaluation Rules For Later Phase C Tasks

Every later Phase C task must be rejected or revised if it does any of the
following:

- Reopens whether the architecture is recordset-first.
- Lets `OdooClient`, `OdooModel`, or `OdooQuery` grow as independent plugin or
  policy control paths.
- Turns plugin hooks into a framework-style arbitrary interception model.
- Makes typed adapters the default or only behavior for normal model access.
- Pushes tracing, retry, timeout, or telemetry logic directly into facade,
  model, query, or recordset methods.
- Treats async as implicit scope or ships public async API surface without a
  later dedicated approved phase.
- Requires CI, hosted observability, remote plugin infrastructure, packaging,
  or release automation to declare Phase C complete.
- Reopens package boundaries in ways that conflict with the current
  `odoo_sdk.odoo_service` naming and module layout without a separate
  architectural decision.

Every later Phase C task should be able to answer these questions directly:

1. Which preserved public surface stays usable after this task?
2. Which Phase C boundary owns the new behavior?
3. Does the task keep the synchronous facade as the default supported path?
4. Does the task route through one canonical plugin-aware or policy-aware path
   rather than creating a parallel implementation?
5. Does the task avoid widening Phase C with work that is explicitly deferred?

## Traceability To Phase C Tasks

| Contract area | Primary follow-on tasks |
|---|---|
| Guardrail and scope lock | C0 |
| Plugin contracts and prohibited zones | C1 |
| Centralized plugin-aware routing | C2 |
| Optional typed adapter strategy | C3 |
| Execution policy boundary | C4 |
| Async boundary evaluation | C5 |
| Documentation and extension guidance | C6 |
| Local validation and scale-phase readiness | C7 |

## Alignment Notes

This contract is intentionally narrower than the longer-term architecture plan.

- The architecture plan describes the multi-phase target trajectory.
- This contract describes the decisions that must hold during Phase C
  implementation.
- The Phase A and Phase B contracts remain in force where this contract does not
  narrow them further.
- If a later Phase C task appears to conflict with this document, the task PRD
  or checklist should be revised before implementation proceeds.