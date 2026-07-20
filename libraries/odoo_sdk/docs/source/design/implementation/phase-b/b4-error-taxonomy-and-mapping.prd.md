# Feature Name

Explicit SDK Error Taxonomy and Mapping

> **Status: accurate, with one addition (2026-07 audit).** The taxonomy below shipped in `src/odoo_sdk/transport/errors.py` and is exported from `src/odoo_sdk/__init__.py`. One class was added afterwards and is not listed here: `DeletionNotSupportedError`, raised by the single transport guard for every `unlink` call (PR #188).

# Goal

## Problem

As long as the SDK surfaces generic runtime failures, consumers cannot respond predictably to common Odoo failure modes such as bad credentials, access denials, validation faults, missing records, or transport disruptions. Phase B adds semantic behavior around metadata and field handling, which raises the cost of leaving error semantics implicit. If each layer maps or wraps failures independently, the public behavior will fragment across model, query, and recordset flows. The SDK needs one explicit error taxonomy and one shared mapping boundary.

## Solution

Introduce a small hierarchy of SDK-defined exceptions and map XML-RPC faults plus transport-level failures into those exceptions at one execution boundary. The taxonomy must cover authentication, access, validation, missing-record, and transport failures explicitly, with a documented base error for broader catches and a fallback server error for unmapped faults. This gives consumers predictable control flow without changing the underlying XML-RPC wire protocol.

# Requirements

## Functional Requirements

- The SDK must define a base SDK exception type for Odoo-facing failures.
- The SDK must define explicit subclasses for authentication failures, access failures, validation failures, missing-record failures, and transport failures.
- The SDK must define a documented fallback server-side error class for unmapped or unexpected XML-RPC faults so generic `RuntimeError` is no longer the primary public failure surface.
- Authentication failures during login or executor setup must map to the authentication error type.
- XML-RPC faults that represent Odoo access errors must map to the access error type.
- XML-RPC faults that represent Odoo validation errors must map to the validation error type.
- XML-RPC faults that represent missing-record behavior must map to the missing-record error type.
- Network, protocol, or local transport execution failures must map to the transport error type.
- Error mapping must occur at one shared execution boundary so model, query, recordset, and compatibility paths observe the same exception semantics.
- The mapped exception types must preserve the most useful original context for debugging, such as fault code, fault string, or underlying exception cause, without leaking credentials.
- Compatibility behavior must be documented for callers that currently expect generic runtime failures, including the recommendation that they catch the new base SDK error when broad handling is desired.
- Local unit tests must cover each mapped error category plus at least one fallback unmapped-fault scenario.

## Non-Functional Requirements

- Error mapping must remain consistent across all high-level SDK entry points.
- The taxonomy must stay small and practical rather than mirroring every server exception type in Odoo.
- The implementation must not require a transport rewrite or protocol change.
- Error details must remain safe for local logging and debugging without exposing secrets.

# Acceptance Criteria

- [ ] The SDK exposes a documented base error plus explicit subclasses for auth, access, validation, missing-record, transport, and fallback server errors.
- [ ] Authentication failures no longer surface primarily as generic runtime exceptions.
- [ ] XML-RPC fault categories map consistently to the documented SDK exceptions.
- [ ] Transport-level failures map consistently to the documented transport error.
- [ ] Model, query, recordset, and compatibility paths all observe the same mapped exception behavior.
- [ ] Unit tests cover every mapped category and an unmapped fallback case.

# Out of Scope

- Retry policy.
- Circuit breaking or advanced resilience policies.
- Tracing, telemetry pipelines, or remote error reporting.
- A one-to-one SDK exception class for every possible Odoo server exception.
