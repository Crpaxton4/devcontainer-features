# Feature Name

Phase B Field Adaptation Layer

# Goal

## Problem

Phase A preserves explicit raw extraction behavior, which is valuable for compatibility but still leaves most Odoo semantics exposed directly to consumers. Many2one tuples, x2many id lists, date strings, datetime strings, and binary payloads all require Odoo-specific decoding that would otherwise be reimplemented at every call site. If the SDK adds these interpretations inconsistently or silently changes every raw read path, it will create new confusion instead of reducing it. Phase B needs one explicit adaptation layer that adds semantic value while preserving the Phase A promise that raw extraction remains available where documented.

## Solution

Introduce a shared field-adaptation layer that consumes raw record payloads plus cached metadata and produces predictable SDK-facing values for the Phase B field categories in scope. Adaptation must be metadata-driven, centrally invoked, and compatible with recordset-first architecture, while raw extraction paths preserved by Phase A remain explicit and documented. This lets the SDK offer richer semantics without forcing every consumer to understand Odoo wire formats directly.

# Requirements

## Functional Requirements

- The SDK must define one shared adaptation layer that accepts raw field values plus field metadata and applies Phase B normalization rules centrally.
- The adaptation layer must support the following field categories in Phase B: many2one, one2many, many2many, date, datetime, and binary.
- many2one adaptation must produce an SDK-defined relation value that preserves at minimum the related model identity and related id, and it must preserve the display label when Odoo includes one.
- one2many and many2many read-side adaptation must produce an SDK-defined relation collection that preserves the related model identity and ordered related ids.
- Date adaptation must normalize supported values into Python `date` objects when the source value is present and well-formed.
- Datetime adaptation must normalize supported values into Python `datetime` objects with explicit UTC semantics when the source value is present and well-formed.
- Binary adaptation must normalize supported values into Python `bytes` when the source value is present and valid base64 data.
- Null, falsey, or empty field values from Odoo must normalize predictably for each supported field category and must not raise accidental parsing errors for standard empty payloads.
- The adaptation layer must define deterministic fallback behavior when metadata is unavailable, a field type is unsupported, or a value cannot be parsed cleanly.
- Phase B must keep raw extraction behavior explicit where Phase A already promised it; richer adapted reads must coexist with those raw paths rather than silently replacing them.
- Recordset-centered behavior that exposes semantic field values must use this shared adaptation layer instead of ad hoc per-call decoding.
- Compatibility paths that expose adapted data must delegate to the same adaptation logic so relation and date semantics do not diverge between surfaces.
- Local unit tests must cover each supported field category, null or empty handling, parse failures, and fallback behavior.

## Non-Functional Requirements

- Adaptation behavior must be deterministic for a given metadata definition and raw value.
- The design must avoid introducing typed model adapters, plugin protocols, or implicit lazy field loading in Phase B.
- The implementation must keep raw and adapted behavior clearly documented so compatibility does not become ambiguous.
- The adaptation layer must be narrow enough that Phase C plugin work can extend it rather than replace it.

# Acceptance Criteria

- [ ] A shared adaptation path exists for many2one, one2many, many2many, date, datetime, and binary values.
- [ ] many2one adaptation preserves related record identity and available display-label information.
- [ ] x2many read-side adaptation preserves related model identity and ordered ids.
- [ ] Date and datetime normalization return Python date-like objects with documented semantics.
- [ ] Binary normalization returns Python `bytes` for valid payloads and handles empty values predictably.
- [ ] Raw extraction behavior preserved by Phase A remains available and explicitly documented.
- [ ] Unit tests cover each in-scope field category plus empty and failure scenarios.

# Out of Scope

- Typed model-specific adapters.
- Plugin-provided adapter registration.
- Implicit lazy loading on attribute access.
- Adapting every Odoo field type beyond the Phase B categories in scope.
