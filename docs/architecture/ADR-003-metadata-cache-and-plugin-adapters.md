# ADR-003 - Add Metadata Caching and Plugin-Based Field Adaptation

Status: Proposed

Date: 2026-05-21

## Context

- Odoo field semantics depend heavily on metadata returned by `fields_get`.
- The current SDK exposes `fields_get`, but it does not use that metadata to adapt values or provide richer semantics.
- Returning raw wire values forces every consumer to understand many2one tuples, x2many command tuples, and field-specific normalization rules.
- Different consumers may need different adapters for selected models or custom modules.

## Decision

- Add an in-memory metadata cache keyed by model and requested attributes.
- Build field and value adapters on top of cached metadata.
- Support narrow plugin hooks or protocols for model-specific adapters and serializers.
- Treat read-side field adaptation as mandatory for ORM parity and write-side command helpers as the next step.

## Consequences

Positive consequences
- The SDK can translate Odoo wire payloads into richer Python-facing semantics.
- Repeated metadata lookups are reduced.
- Custom extensions gain stable hook points without modifying core objects directly.

Negative consequences
- Cache invalidation and version drift need careful testing.
- The SDK must define when it returns raw payloads and when it returns adapted values.

## Rejected alternatives

- Keep returning raw values everywhere.
  - Rejected because it pushes too much Odoo-specific protocol knowledge into consumers.

- Hard-code adapters without metadata.
  - Rejected because Odoo model and field behavior is dynamic and extensible.
