# ADR-001 - Adopt a Recordset-First Public API

Status: Accepted

Date: 2026-05-21

> **Reconciliation note (2026-07).** The recordset-first *direction* decided here
> shipped; three of the type names it proposed did not. `OdooEnv`, `OdooModel`,
> and `OdooQuery` were **never shipped under those names** — `grep` for them in
> `src/odoo_sdk` returns nothing and none appear in `odoo_sdk.__all__`. The
> shipped high-level public surface is `OdooClient`, `OdooRecordset`, and
> `DomainExpression`: `OdooRecordset` carries execution context inline (so no
> separate env object was needed) and replaced the historical thin wrappers
> outright rather than keeping them as compatibility layers. The *Context* and
> *Decision* sections below are retained as the 2026-05-21 record and still name
> the planning-era types; *Implementation Status* has been reconciled to what
> actually shipped. See the
> [architecture plan](../odoo-sdk-architecture-plan.md) for the same
> reconciliation across the wider design set.

## Context

- The project goal is to mirror Odoo ORM semantics over the external XML-RPC API.
- The supported public surface is centered on `OdooClient`, with `OdooModel` and `OdooQuery` as the thin model-handle and query-builder wrappers beneath it.
- The current abstractions return ids and raw row dictionaries, but not a stable object that carries model identity, record ids, and execution context.
- Odoo itself centers the ORM around recordsets, not around standalone query builders.

## Decision

- Introduce `OdooEnv` and `OdooRecordset` as the primary high-level public abstractions.
- Keep `OdooClient` as the main entry point and facade.
- Keep `OdooModel` and `OdooQuery` as compatibility layers during migration.
- Route future ORM-like features, including `with_context`, relation traversal, field adapters, and x2many helpers, through recordsets rather than model helper methods.

## Implementation Status

Reconciled against the shipped package 2026-07.

- `OdooClient`, `DomainExpression`, and `OdooRecordset` are public exports (`odoo_sdk.__all__`).
- `OdooClient["model"]` returns a cached empty model-bound `OdooRecordset` (`client/client.py`); the client itself is the model registry, so no separate env object is indexed.
- `OdooRecordset.search(...)` returns `OdooRecordset` directly (`records/recordset.py`).
- `browse(...)` binds ids to `OdooRecordset` identity rather than returning row payloads.
- Explicit `read()` and `read_adapted()` remain available as low-level extraction helpers.
- The proposed `OdooEnv` type was **not** built: `OdooRecordset` carries context inline via `with_context` / `with_company`.
- The `OdooModel` and `OdooQuery` compatibility layers were **not** retained. The recordset-first core replaced them outright, so no migration shims exist.

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
