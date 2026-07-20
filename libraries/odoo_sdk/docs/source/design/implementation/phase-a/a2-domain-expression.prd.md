# Feature Name

DomainExpression Canonical Domain Boundary

> **Status: accurate, with corrections (2026-07 audit).** `DomainExpression` shipped and is the single normalization and serialization boundary (`src/odoo_sdk/query/domain.py`); unlike the Phase A export decision recorded in A6, it is now a supported public export. One requirement below no longer has a subject: `OdooModel` and `OdooQuery` are absent from the shipped SDK, so only `OdooRecordset` routes through the canonical path.

# Goal

## Problem

The current public domain type is a raw list-of-tuples alias. That shape is simple for narrow `search` calls, but it does not establish a single canonical representation for domain logic and it does not naturally cover Odoo's boolean prefix operators or future nested expressions. As a result, domain handling is under-structured and risks being reimplemented in multiple places. The SDK needs one abstraction that owns normalization and serialization even if the public compatibility path remains broad during Phase A.

## Solution

Introduce `DomainExpression` as the Phase A boundary for domain normalization and XML-RPC serialization. Existing callers may continue passing current domain inputs during the transition, but all search-oriented code paths must normalize those inputs through one canonical domain abstraction before execution. This keeps compatibility intact while establishing a clear home for future domain semantics.

# Requirements

## Functional Requirements

- A new `DomainExpression` abstraction must exist and represent the canonical internal form of a domain used by Phase A search operations.
- `DomainExpression` must define one normalization entry point for converting supported caller inputs into the canonical form.
- The Phase A compatibility path must continue to accept existing list-of-tuples domain inputs at current search-oriented entry points.
- The abstraction must support serializing normalized domains into the XML-RPC-compatible payload shape expected by the current transport layer.
- The design must accommodate Odoo boolean prefix operators and nested domain structures without requiring Phase A to ship a full boolean algebra builder DSL.
- `OdooModel`, `OdooQuery`, and `OdooRecordset` search-related behavior must route domain inputs through the canonical normalization and serialization path.
- Empty-domain behavior must be defined explicitly and remain compatible with existing search behavior by normalizing and serializing to the current search-all payload shape.
- Validation rules for unsupported or malformed domain shapes must be defined so failure modes are predictable.
- Local unit tests must cover normalization and serialization of empty domains, simple list-of-tuples inputs, and at least one compound boolean-expression input.

## Non-Functional Requirements

- The abstraction must avoid over-designing a domain DSL beyond Phase A needs.
- Serialization must be deterministic for a given normalized expression.
- Compatibility for current caller inputs must take precedence over adding new fluent domain-construction APIs.
- The implementation must keep domain logic centralized rather than duplicating normalization in multiple call sites.

# Acceptance Criteria

- [ ] Existing callers can still pass the current list-of-tuples domain format to preserved public search entry points.
- [ ] Domain inputs are normalized through a single `DomainExpression` path before XML-RPC execution.
- [ ] A normalized domain can be serialized into the exact wire-compatible structure required by the current executor path.
- [ ] Empty domains normalize and serialize in a defined, test-covered way that preserves current search-all semantics.
- [ ] At least one test demonstrates correct handling of a compound domain that includes boolean operator structure.
- [ ] Invalid domain shapes fail in a predictable, documented way.

# Out of Scope

- A full user-facing domain-construction DSL.
- Metadata-aware domain validation.
- Query optimization, simplification, or algebraic rewriting beyond basic normalization.
- Any field adaptation or x2many command support.
