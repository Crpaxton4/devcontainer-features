# Feature Name

Plugin-Aware Internal Wiring

> **Status: never implemented (2026-07 audit).** No part of Phase C shipped. `src/odoo_sdk/` contains no plugin contract, plugin registry, typed-adapter layer, or execution-policy seam for tracing, retry, timeout, or telemetry, and there is no async facade. The `adapters/` package is unrelated to this phase: it holds `state_persistence.py` and `external_sync.py` for the task-tracker. Retained as a record of the original Phase C plan. There is no plugin registration, discovery, or precedence path to wire.

# Goal

## Problem

Plugin contracts alone do not create usable extensibility. If recordsets, metadata adapters, `OdooModel`, and `OdooQuery` each discover or apply plugins differently, the SDK will produce inconsistent results and duplicate control paths. That inconsistency would be especially dangerous because compatibility layers could quietly bypass the same hooks that the recordset-first core uses, leading to divergent semantics based on entry point rather than business intent. Phase C needs one coherent internal routing model for plugin-aware execution.

## Solution

Add centralized plugin-aware wiring through the existing Phase A and Phase B internals. Registration, discovery, ordering, and application should occur through one coherent internal path so that recordsets and preserved compatibility surfaces share the same extension behavior. This makes extensibility predictable without fragmenting the architecture.

# Requirements

## Functional Requirements

- The implementation must define how plugins are registered for local runtime use.
- The implementation must define how plugin discovery occurs at runtime, including whether discovery is explicit, configuration-driven, or registry-based.
- The implementation must define deterministic plugin ordering and precedence rules when more than one plugin applies to the same model or hook category.
- Plugin-aware behavior must route through one coherent internal path centered on the existing recordset-first and metadata-aware architecture.
- `OdooModel` and `OdooQuery` compatibility behavior must use the same plugin-aware internals rather than bypassing them.
- The internal design must prevent double-application of the same plugin behavior across nested execution paths.
- The implementation must define how no-plugin and multiple-plugin scenarios behave for each supported hook category.
- The implementation must define how plugin registration state is exposed or inspected for local diagnostics and testability.
- The implementation must define how plugin-aware behavior interacts with Phase B caches, adapters, and explicit error mapping.
- Local regression tests must cover registration, discovery, precedence, compatibility-layer routing, and consistent behavior across main entry points.

## Non-Functional Requirements

- Plugin-aware execution must be deterministic for a given registry state.
- The design must avoid hidden global behavior that is difficult to reset in tests.
- The wiring must reduce duplication rather than creating a second extension stack beside the main architecture.
- The implementation must stay local-runtime friendly and must not require network-based plugin discovery.
- The internal flow must remain readable enough that maintainers can identify the single canonical plugin path.

# Acceptance Criteria

- [ ] Plugins can be registered and discovered through one documented runtime path.
- [ ] Ordering and precedence rules are documented and covered by tests.
- [ ] Recordset-first internals and compatibility surfaces share the same plugin-aware control path.
- [ ] Tests prove that plugin behavior is not applied twice when the same operation passes through layered abstractions.
- [ ] Local diagnostics or tests can inspect the effective plugin registration state.

# Out of Scope

- Remote plugin loading or per-request plugin downloads.
- A full dependency-resolution system for plugins.
- Plugin-specific UI, CLI management tools, or hosted registries.
- Rewriting compatibility surfaces into separate plugin-aware architectures.
