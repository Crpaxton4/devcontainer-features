# ADR-003 - Add Metadata Caching and Internal Field Adaptation

Status: Proposed

Date: 2026-05-21

## Context

- Odoo field semantics depend heavily on metadata returned by `fields_get`.
- The current SDK exposes `fields_get`, but it does not use that metadata to adapt values or provide richer semantics.
- Returning raw wire values forces every consumer to understand many2one tuples, x2many command tuples, and field-specific normalization rules.
- Phase A intentionally preserved raw extraction behavior and deferred semantic interpretation until a shared internal path existed.
- Plugin-defined or model-specific extension behavior is a later concern than the Phase B semantic baseline.

## Decision

- Add an in-memory metadata cache keyed by model, requested field set,
  requested attribute set, and the runtime context that materially affects
  `fields_get` payloads.
- Build shared internal field and value adaptation boundaries on top of cached metadata.
- Keep raw extraction behavior explicit while documenting where adapted behavior coexists with it.
- Defer plugin hooks or protocols for model-specific adapters and serializers to Phase C.
- Treat read-side field adaptation as mandatory for ORM parity and write-side command helpers as the next step.

## Consequences

Positive consequences
- The SDK can translate Odoo wire payloads into richer Python-facing semantics.
- Repeated metadata lookups are reduced.
- Recordset flows and compatibility flows can share the same semantic internals instead of growing parallel logic.

Negative consequences
- Cache invalidation and version drift need careful testing.
- The SDK must define when it returns raw payloads and when it returns adapted values.
- Extensibility for model-specific behavior remains deferred until Phase C.

## Rejected alternatives

- Keep returning raw values everywhere.
  - Rejected because it pushes too much Odoo-specific protocol knowledge into consumers.

- Hard-code adapters without metadata.
  - Rejected because Odoo model and field behavior is dynamic and extensible.

- Introduce plugin contracts in the same phase as metadata and field adaptation.
  - Rejected because it would mix semantic growth with extensibility work before the shared internal boundaries are proven.
