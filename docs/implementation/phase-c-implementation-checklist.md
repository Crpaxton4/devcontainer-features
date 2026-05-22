# Phase C Implementation Checklist

## Objective

Implement the minimum scale-phase changes needed to make the SDK intentionally extensible and operationally robust by adding plugin hooks, optional typed adapters for stable internal models, execution policy hooks for tracing and retry behavior, and a documented boundary for any future async facade.

## PRD-Ready Context

### Problem statement

Phase B makes the SDK semantically useful, but Phase B still assumes one dominant internal behavior path. Once multiple consumers depend on the SDK, maintainers need stable extension seams, clearer operational hooks, and a deliberate way to evaluate async support without destabilizing the synchronous core.

### Desired outcome

- Consumers and maintainers have a narrow, documented plugin surface for model-specific behavior.
- Stable internal model families can opt into typed adapters without turning the whole SDK into a code-generated system.
- Execution policies such as tracing, retry, timeout control, and richer telemetry have a clear hook boundary.
- Any async work is evaluated and, if adopted later, kept separate from the synchronous facade.
- All of the above remain compatible with the local-tooling-only workflow.

### Non-goals

- No forced migration from the synchronous API to an async API.
- No broad code generation strategy for all Odoo models.
- No hosted observability platform rollout.
- No CI, package publishing, or release automation work.
- No redesign of the recordset-first core introduced in earlier phases.

### Constraints

- Preserve established public surfaces: `OdooClient`, `OdooModel`, `OdooQuery`, `OdooExecutor`, and `CommandDispatcher` must remain usable.
- Build on Phase A and Phase B abstractions rather than reopening the core architecture.
- Keep plugin hooks narrow and typed enough to avoid creating a "run arbitrary logic everywhere" extension model.
- Keep async support separate from the sync API if it moves forward.
- Keep tooling local-only.

### Success signal

- Plugin hook points exist and are documented.
- Optional typed adapters can be added for selected stable internal models without changing the default dynamic architecture.
- Execution policy behavior has a clear wrapping or hook strategy rather than ad hoc branching.
- A documented async decision boundary exists and does not leak into the sync facade.
- Phase C can be validated locally through tests, examples, and documented workflows.

## Execution Order

1. Lock down Phase C boundaries and extension goals.
2. Introduce plugin contracts and registration rules.
3. Introduce plugin-aware wiring through the existing internals.
4. Add optional typed adapter support for selected stable models.
5. Add execution policy hooks for tracing, retry, timeout, and telemetry.
6. Evaluate the separate async facade boundary.
7. Update docs, local validation, and exit criteria.

## Implementation Checklist

## C0 - Phase Guardrails

Goal
- Define the exact Phase C contract before extensibility and operational hooks expand the surface area.

Likely touch points
- `docs/odoo-sdk-architecture-plan.md`
- `docs/odoo-sdk-design-patterns.md`
- `docs/implementation/phase-b-implementation-checklist.md`
- New Phase C modules and tests

Checklist
- [ ] Confirm the exact Phase C scope: plugin hooks, optional typed adapters, execution policy hooks, and async boundary evaluation.
- [ ] Confirm the Phase B prerequisites that must exist before Phase C starts.
- [ ] Confirm that the synchronous public facade remains the default path.
- [ ] Confirm which concerns remain out of scope even in Phase C.

Done when
- The implementation team can state precisely what Phase C adds and what is still intentionally deferred.

## C1 - Define Plugin Contracts and Extension Boundaries

Goal
- Create narrow hook contracts for model-specific behavior and consumer extensions.

Why this exists
- Phase C needs intentional extensibility. Without explicit contracts, extension behavior will spread through ad hoc overrides and compatibility hacks.

Likely touch points
- New plugin protocol or hooks modules
- Recordset, model, and metadata-related internals introduced in earlier phases
- Documentation for extension boundaries

Checklist
- [ ] Define the minimum plugin hook categories needed in Phase C.
- [ ] Define where plugin hooks are allowed to run and where they are explicitly not allowed to run.
- [ ] Define plugin input and output contracts clearly enough for maintainers to validate compatibility.
- [ ] Define how plugin failures are handled so they do not silently corrupt execution flow.
- [ ] Add local tests for hook registration and hook contract enforcement.

Done when
- There is one documented plugin contract model that supports extension without encouraging arbitrary deep coupling.

PRD inputs captured by this item
- User-visible behavior change: consumers gain stable extension seams.
- Main technical risk: making the plugin API too broad or too magical.

## C2 - Add Plugin-Aware Internal Wiring

Goal
- Route the plugin contracts through the existing Phase A and Phase B internals without fragmenting the architecture.

Why this exists
- Plugin contracts are only useful if the main execution paths can discover and apply them predictably.

Likely touch points
- Recordset internals
- Metadata cache and adapter internals
- Model compatibility helpers
- New plugin registry or configuration support

Checklist
- [ ] Define how plugins are registered and discovered in a local runtime.
- [ ] Define how plugin ordering or precedence works if more than one extension applies.
- [ ] Ensure plugin-aware behavior routes through one coherent internal path.
- [ ] Ensure compatibility surfaces (`OdooModel`, `OdooQuery`) keep using the same plugin-aware internals rather than bypassing them.
- [ ] Add local regression tests for plugin-aware behavior across the main entry points.

Done when
- Plugin-aware execution is centralized and compatible with both the recordset-first core and the preserved compatibility layers.

PRD inputs captured by this item
- User-visible behavior change: extension behavior becomes available without monkey-patching internals.
- Main technical risk: duplicate plugin application paths creating inconsistent outcomes.

## C3 - Add Optional Typed Adapters for Stable Internal Models

Goal
- Support optional typed adapters for selected stable internal models without turning the SDK into a code-generated or fully static architecture.

Why this exists
- Some internal consumers may benefit from stronger typing and richer IDE support for a narrow, stable model subset.

Likely touch points
- New typed adapter modules
- Plugin or adapter registration logic
- Recordset read and projection paths
- Local tests and docs for selected stable models

Checklist
- [ ] Define which models are stable enough to justify typed adapters in Phase C.
- [ ] Define how typed adapters coexist with the default dynamic model behavior.
- [ ] Define how typed adapters are selected or registered without breaking callers that expect dynamic results.
- [ ] Add local tests for adapter selection, typed output behavior, and fallback to dynamic behavior.

Done when
- Selected stable models can use typed adapters without forcing the whole SDK into a typed-only model.

PRD inputs captured by this item
- User-visible behavior change: selected consumers can opt into stronger typed ergonomics.
- Main technical risk: letting typed adapters become a second competing architecture.

## C4 - Add Execution Policy Hooks for Tracing, Retry, Timeout, and Telemetry

Goal
- Introduce a clear wrapping or hook boundary for execution policies that affect observability and resilience.

Why this exists
- As usage grows, maintainers need one place to add tracing, timing, retry, timeout, and richer telemetry behavior instead of scattering those concerns across transport, model, and recordset code.

Likely touch points
- Executor or session wrappers
- Transport internals
- Error mapping internals
- Local tooling and diagnostics docs

Checklist
- [ ] Define the execution policy boundary: wrapper, decorator, or session-level hook path.
- [ ] Define how tracing and timing information are captured in a local-tooling-first workflow.
- [ ] Define how retry and timeout policy are configured and where those decisions live.
- [ ] Define how telemetry is exposed without requiring a hosted observability platform.
- [ ] Add local tests for policy application and failure behavior.

Done when
- Cross-cutting execution behavior can be added or adjusted in one place without widening model and recordset responsibilities.

PRD inputs captured by this item
- User-visible behavior change: operational behavior becomes more configurable and observable.
- Main technical risk: coupling policy hooks too tightly to one transport implementation.

## C5 - Evaluate the Separate Async Facade Boundary

Goal
- Produce a clear go or no-go decision and an architectural boundary for any future async support.

Why this exists
- Async is valuable only if it can be added without destabilizing the synchronous facade or duplicating business semantics.

Likely touch points
- Architecture docs
- Executor or session abstraction review
- Local experiments or proof-of-concept notes if needed

Checklist
- [ ] Define the criteria for when async support is justified.
- [ ] Define whether the current transport and session seams are sufficient to support a separate async path later.
- [ ] Define what must remain shared between sync and async behavior.
- [ ] Define what must remain separate to avoid semantic drift.
- [ ] Record a Phase C decision: defer async, prototype async, or approve a later dedicated async phase.

Done when
- The project has a documented async boundary and a clear decision about whether async belongs in a later implementation phase.

PRD inputs captured by this item
- User-visible behavior change: none required immediately.
- Main technical risk: prematurely committing to async and duplicating the sync architecture.

## C6 - Update Documentation and Extension Guidance

Goal
- Ensure the Phase C extensibility and operational model are documented clearly enough for later PRDs and implementation work.

Why this exists
- Phase C adds the first deliberate long-term extension seams. Those seams need clear boundaries or they will be misused quickly.

Likely touch points
- `docs/odoo-sdk-architecture-plan.md`
- `docs/odoo-sdk-design-patterns.md`
- `docs/implementation/phase-c-implementation-checklist.md`
- ADRs if Phase C decisions need formalization later

Checklist
- [ ] Document the plugin boundary and allowed extension points.
- [ ] Document the optional typed adapter strategy and when it should be used.
- [ ] Document the execution policy hook boundary for local workflows.
- [ ] Document the async evaluation outcome.
- [ ] Keep all docs aligned with the local-tooling-only workflow.

Done when
- Docs, architecture guidance, and implementation boundaries all describe the same Phase C model.

## C7 - Local Validation and Scale-Phase Readiness

Goal
- Ensure the full Phase C feature set is locally testable and ready for later PRDs or implementation reviews.

Why this exists
- Phase C is where the SDK becomes intentionally extensible. That requires a strong local validation path before maintainers rely on those seams.

Likely touch points
- `tests/`
- Existing local scripts and tooling
- Examples or local extension scenarios
- Docs under `docs/`

Checklist
- [ ] Add or update unit tests for plugin contracts and execution.
- [ ] Add or update tests for optional typed adapter behavior.
- [ ] Add or update tests for execution policy hooks.
- [ ] Add local validation scenarios that prove plugin-aware behavior and policy-aware behavior end to end.
- [ ] Confirm that all Phase C validation remains local-only.

Done when
- A maintainer can validate the Phase C feature set end to end with local tooling and documented workflows only.

## Exit Criteria

- [ ] Narrow plugin hook contracts exist and are documented.
- [ ] Plugin-aware internal wiring exists and uses one coherent execution path.
- [ ] Optional typed adapters work for selected stable internal models without displacing the default dynamic model behavior.
- [ ] Execution policy hooks exist for tracing, retry, timeout, and local telemetry use cases.
- [ ] The async facade boundary has a documented decision and does not destabilize the sync facade.
- [ ] Local tests cover the new Phase C extensibility and operational behaviors.
- [ ] Phase C docs are sufficient to draft later PRDs without revisiting the implementation baseline.
