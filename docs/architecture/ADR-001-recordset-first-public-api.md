# ADR-001 - Adopt a Recordset-First Public API

Status: Accepted

Date: 2026-05-21

## Context

- The project goal is to mirror Odoo ORM semantics over the external XML-RPC API.
- The supported public surface is now centered on `OdooClient`, `OdooEnv`, `DomainExpression`, and `OdooRecordset`, while `OdooModel` and `OdooQuery` remain compatibility layers.
- The current abstractions return ids and raw row dictionaries, but not a stable object that carries model identity, record ids, and execution context.
- Odoo itself centers the ORM around recordsets, not around standalone query builders.

## Decision

- Introduce `OdooEnv` and `OdooRecordset` as the primary high-level public abstractions.
- Keep `OdooClient` as the main entry point and facade.
- Keep `OdooModel` and `OdooQuery` as compatibility layers during migration.
- Route future ORM-like features, including `with_context`, relation traversal, field adapters, and x2many helpers, through recordsets rather than model helper methods.

## Implementation Status

- `OdooEnv`, `DomainExpression`, and `OdooRecordset` are now public exports.
- `OdooClient["model"]` and `OdooEnv["model"]` now return empty model-bound `OdooRecordset` instances.
- `OdooRecordset.search(...)` and `OdooModel.search(...)` now return `OdooRecordset` directly.
- `browse(...)` now binds ids to `OdooRecordset` identity rather than returning row payloads.
- Explicit `read()` and `read_adapted()` remain available as low-level extraction helpers.
- `OdooModel` and `OdooQuery` remain available only as compatibility-oriented surfaces over the recordset-owned core.

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
