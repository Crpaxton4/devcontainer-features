# Phase A Architectural Contract

## Purpose

This contract is the implementation baseline for Phase A.

Its job is to keep the SDK moving from a query-builder-first internal shape toward a recordset-first core without removing or breaking established public entry points. Every later Phase A task should be evaluated against this document before it is accepted.

## Preserved Public Surfaces

The following public surfaces must remain usable throughout Phase A:

| Surface | Phase A status | Guardrail |
|---|---|---|
| `OdooClient` | Preserved | Remains the top-level facade and package story |
| `OdooModel` | Preserved | Remains usable as a proxy and compatibility wrapper |
| `OdooQuery` | Preserved | Remains usable as a transitional fluent compatibility shim |
| `OdooExecutor` | Preserved | Remains the execution seam for transport behavior |
| `CommandDispatcher` | Preserved | Remains usable as an integration helper, not the ORM center |

Phase A must not remove these entry points, force callers onto replacement APIs, or widen them with unrelated new responsibilities.

## Responsibility Boundaries

Each Phase A abstraction has one primary ownership boundary.

| Abstraction | Owns | Does not own |
|---|---|---|
| `OdooClient` | Top-level facade, client construction, model access entry point | Domain logic, context mutation, record identity |
| `OdooEnv` | Execution context, environment derivation, model lookup path | Session policy, retry behavior, metadata caching, field adaptation |
| `DomainExpression` | Domain normalization and XML-RPC serialization | User-facing DSL growth, optimization, semantic rewriting |
| `OdooRecordset` | Model identity, ordered ids, env-bound record operations | Metadata-driven field semantics, lazy relation traversal, x2many helpers |
| `OdooModel` | Proxy and compatibility wrapper over Phase A primitives | Long-term ORM center, duplicate search and browse control paths |
| `OdooQuery` | Immutable fluent compatibility layer for current call sites | Long-term architectural center, new query DSL scope |

## Resolved Phase A Decisions

### Context and Environment

- `OdooEnv` is the Phase A owner of execution context and environment derivation.
- `with_context` behavior must derive a new environment or recordset rather than mutating existing objects in place.
- Caller-provided context data must be defensively copied so later mutation does not leak back into stored state.
- `OdooSession` is not a Phase A deliverable. Any fuller session, retry, timeout, or policy abstraction is deferred beyond the Phase A environment boundary.

### Domain Boundary

- `DomainExpression` is the single normalization and serialization path for search domains in Phase A.
- Preserved public search entry points may continue accepting current compatibility inputs such as list-based domain structures.
- Empty domains must preserve current search-all behavior after normalization and serialization.
- Phase A must support canonical handling of nested and boolean-prefixed domain structures without introducing a broader public domain-builder DSL.

### Record Identity

- `OdooRecordset` is the identity-bearing core introduced in Phase A.
- A recordset must carry model name, ordered ids, and an env.
- `read()` remains the explicit raw extraction path for callers that need dictionaries.
- `search`, `browse`, `exists`, and `with_context` on the recordset-oriented core are recordset operations, not query-builder replacements.

### Compatibility Behavior

- Phase A moves the architectural center underneath preserved surfaces before it changes preserved caller-facing semantics.
- `OdooModel` and `OdooQuery` must route through the canonical env, domain, and recordset path once those primitives exist.
- During Phase A, preserved wrapper surfaces keep current caller-facing behavior unless a later Phase A PRD explicitly documents a compatibility change and its tests.
- `OdooQuery` is transitional by design. It remains available for fluent compatibility, but it is not the long-term center of ORM behavior.

### Public Export Scope

- Phase A does not widen the supported public package exports beyond the preserved surfaces.
- `OdooEnv`, `DomainExpression`, and `OdooRecordset` remain internal Phase A primitives.
- Package `__all__` declarations stay centered on `OdooClient`, `OdooModel`, `OdooQuery`, `OdooExecutor`, `OdooRpcExecutor`, `OdooConnectionSettings`, `CommandDispatcher`, and the existing utility aliases.
- Importing the Phase A primitives from implementation modules may remain possible for local development, but that does not make them supported top-level public API during Phase A.

## Explicitly Deferred Work

The following work is outside Phase A and must not be smuggled in through compatibility layers or helper abstractions:

- Metadata caching
- Field adapters
- x2many command helpers
- Plugin infrastructure
- Async APIs
- CI automation
- Release automation

The following work is also deferred because it would reopen responsibilities that Phase A is only establishing, not completing:

- A full session abstraction beyond the Phase A environment boundary
- Metadata-aware domain validation
- Typed adapters or code generation
- Hosted or remote validation workflows

## Minimum Success Conditions

Phase A is successful only if all of the following are true:

1. `OdooClient` still works as the top-level facade.
2. Context ownership lives in env-bound or recordset-bound objects rather than inside `OdooQuery` alone.
3. Domain serialization is centralized through `DomainExpression`.
4. Record identity is represented by `OdooRecordset` rather than only by raw ids or rows.
5. `OdooModel` and `OdooQuery` remain usable while delegating toward the new core.
6. Validation remains local-tooling only.

## Evaluation Rules For Later Phase A Tasks

Every later Phase A task must be rejected or revised if it does any of the following:

- Reopens whether the architecture is recordset-first.
- Moves context ownership away from `OdooEnv`.
- Adds a second independent domain normalization path.
- Lets `OdooModel` or `OdooQuery` grow as parallel long-term control paths.
- Pulls deferred Phase B or Phase C work into Phase A.
- Requires CI, hosted services, or release automation to declare Phase A complete.

Every later Phase A task should be able to answer these questions directly:

1. Which preserved surface stays usable after this task?
2. Which Phase A owner is responsible for the new behavior?
3. Does the task keep domain handling on the canonical path?
4. Does the task move control toward env and recordset abstractions rather than away from them?
5. Does the task avoid widening Phase A with deferred work?

## Traceability To Phase A Tasks

| Contract area | Primary follow-on tasks |
|---|---|
| Preserved facade behavior | A1, A4, A5, A6 |
| `OdooEnv` context ownership | A1, A4, A5 |
| `DomainExpression` normalization boundary | A2, A4, A5 |
| `OdooRecordset` identity-bearing core | A3, A4, A5 |
| Transitional `OdooQuery` role | A4, A5, A6 |
| Export decisions deferred to documentation alignment | A6 |
| Local validation only | A7 |

## Alignment Notes

This contract is intentionally narrower than the longer-term architecture plan.

- The architecture plan describes the target trajectory.
- This contract describes the decisions that must hold during Phase A implementation.
- If a later Phase A task appears to conflict with this document, the task PRD or checklist should be revised before implementation proceeds.