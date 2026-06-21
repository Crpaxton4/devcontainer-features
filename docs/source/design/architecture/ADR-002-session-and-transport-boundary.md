# ADR-002 - Introduce a Session and Transport Policy Boundary

Status: Proposed

Date: 2026-05-21

## Context

- `OdooRpcExecutor` already provides a clean transport seam for authentication and `execute_kw` calls.
- The current design does not have a higher-level session abstraction that owns timeout policy, retry behavior, context defaults, or error mapping.
- Future extensibility requires a place to add logging, tracing, secret redaction, and richer exception types without leaking XML-RPC details upward.

## Decision

- Add `OdooSession` above the transport implementation.
- Keep the transport implementation pluggable through an executor or transport protocol.
- Move auth, retry, timeout, and error-mapping policy decisions into the session layer.
- Keep `OdooRpcExecutor` as the default synchronous XML-RPC transport implementation.

## Consequences

Positive consequences
- Transport policy becomes explicit and testable.
- Future observability hooks have a single integration point.
- Additional transports or alternate execution strategies can be introduced without redesigning ORM-facing objects.

Negative consequences
- One more abstraction layer to maintain.
- Error handling will need a small taxonomy instead of generic `RuntimeError` usage.

## Rejected alternatives

- Keep all policy inside `OdooRpcExecutor`.
  - Rejected because transport concerns and ORM-facing policy concerns should evolve independently.

- Push transport policy up into `OdooClient`.
  - Rejected because the client should stay a lightweight facade, not a full session manager.
