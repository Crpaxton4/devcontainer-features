# ADR-001 - Adopt a Recordset-First Public API

Status: Proposed

Date: 2026-05-21

## Context

- The project goal is to mirror Odoo ORM semantics over the external XML-RPC API.
- The current public surface is centered on `OdooClient`, `OdooModel`, and `OdooQuery`.
- The current abstractions return ids and raw row dictionaries, but not a stable object that carries model identity, record ids, and execution context.
- Odoo itself centers the ORM around recordsets, not around standalone query builders.

## Decision

- Introduce `OdooEnv` and `OdooRecordset` as the primary high-level public abstractions.
- Keep `OdooClient` as the main entry point and facade.
- Keep `OdooModel` and `OdooQuery` as compatibility layers during migration.
- Route future ORM-like features, including `with_context`, relation traversal, field adapters, and x2many helpers, through recordsets rather than model helper methods.

## Consequences

Positive consequences
- Public API gains a stable identity-bearing abstraction that aligns with Odoo documentation.
- Context propagation becomes coherent and composable.
- Relation handling and field adaptation gain a natural home.
- Extensibility improves because plugins and adapters can target recordsets and envs instead of raw payloads.

Negative consequences
- Medium refactor cost.
- Some consumers may need to adapt if they depend directly on `OdooQuery` behavior.
- The project will need staged deprecations and compatibility notes.

## Rejected alternatives

- Keep the current query-builder-first design and continue adding helper methods.
  - Rejected because it keeps pushing ORM semantics into ad hoc wrapper methods.

- Generate model-specific clients from metadata first.
  - Rejected because Odoo's model surface is dynamic and version sensitive, especially with custom modules.
