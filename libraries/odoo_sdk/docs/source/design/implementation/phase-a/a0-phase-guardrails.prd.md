# Feature Name

Phase A Guardrails and Architectural Contract

> **Status: superseded (2026-07 audit).** The preserved-surface list below does not describe the shipped SDK. `OdooModel`, `OdooQuery`, and `CommandDispatcher` are absent from `src/odoo_sdk/`, and `OdooEnv` was removed as superseded and un-exported (PR #161). What survived is a recordset-first core: `OdooClient` as facade, `DomainExpression` as the single domain boundary, `OdooRecordset` as the identity-bearing centre, plus the `Command`/`Registry` command layer and the transport executors — see `src/odoo_sdk/__init__.py`. The package is named `odoo_sdk`, not `odoo_service`.

# Goal

## Problem

Phase A is the first architectural refactor that changes the SDK's internal center of gravity without removing any established public entry points. Without an explicit contract, engineering work can drift between two competing designs: the existing query-builder-first surface and the target recordset-first core. That drift would make later tasks inconsistent, especially around ownership of context, domain serialization, and record identity. The team needs a single definition of what Phase A must preserve, what it must introduce, and what it must explicitly defer.

## Solution

Define a written Phase A contract before implementation expands beyond the current `OdooClient`, `OdooModel`, and `OdooQuery` surfaces. This contract will lock the stable surfaces that must remain usable, assign ownership boundaries to `OdooEnv`, `DomainExpression`, and `OdooRecordset`, and document the features that stay deferred until later phases. The result should let the implementation team make Phase A changes without reopening core architecture decisions in every task.

# Requirements

## Functional Requirements

- Phase A must preserve the usability of the current public entry points: `OdooClient`, `OdooModel`, `OdooQuery`, `OdooExecutor`, and `CommandDispatcher`.
- Phase A must define `OdooClient` as the top-level facade and not as the owner of domain logic, context mutation, or record identity.
- Phase A must define `OdooEnv` as the owner of execution context and environment derivation.
- Phase A must define `DomainExpression` as the single normalization and serialization boundary for search domains.
- Phase A must define `OdooRecordset` as the stable identity-bearing abstraction for model name, ids, and env-bound operations.
- Phase A must define `OdooModel` as a proxy and compatibility wrapper rather than the long-term center of ORM behavior.
- Phase A must define `OdooQuery` as a compatibility shim that preserves fluent call sites while no longer acting as the architectural center.
- The contract must explicitly document that Phase A does not include metadata caching, field adapters, x2many command helpers, plugin infrastructure, async APIs, CI automation, or release automation.
- The contract must define the minimum success conditions for the phase: preserved facade behavior, context ownership in env or recordset objects, centralized domain serialization, recordset-based identity, and local validation only.
- The contract must align with the architecture plan and design-pattern guidance already approved for the repository.

## Non-Functional Requirements

- The guardrails must be documented in a way that is precise enough to prevent scope creep during implementation.
- The language must stay consistent with the architecture plan, ADRs, and current package naming.
- The contract must prefer minimal surface expansion and staged compatibility rather than parallel long-term APIs.
- The contract must remain local-tooling friendly and must not introduce any dependency on CI or hosted services.

# Acceptance Criteria

- [ ] The Phase A contract explicitly states which existing public surfaces must remain usable throughout the phase.
- [ ] The contract assigns one primary responsibility boundary each to `OdooClient`, `OdooEnv`, `DomainExpression`, `OdooRecordset`, `OdooModel`, and `OdooQuery`.
- [ ] The contract explicitly states that `OdooQuery` is transitional and not the long-term architectural center.
- [ ] The contract explicitly lists deferred work for metadata caching, field adapters, x2many command helpers, plugins, async behavior, CI, and release automation.
- [ ] The Phase A contract can be used by the implementation team to evaluate every later Phase A task without reopening the core architecture direction.

# Out of Scope

- Implementing `OdooEnv`, `DomainExpression`, or `OdooRecordset`.
- Introducing a session abstraction beyond the Phase A environment boundary.
- Adding new field semantics, relation adapters, or plugin hooks.
- Any packaging, publishing, CI, or operational automation work.
