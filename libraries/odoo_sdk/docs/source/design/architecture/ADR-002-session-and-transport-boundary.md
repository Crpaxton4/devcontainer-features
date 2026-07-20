# ADR-002 - Introduce a Session and Transport Policy Boundary

Status: Rejected

Date: 2026-05-21

Resolved: 2026-07-15

## Resolution

This ADR was left in `Proposed` limbo for over a year. It is now resolved as
**Rejected**: the proposed `OdooSession` policy layer was never built, and the
codebase deliberately settled into the alternative this ADR had listed as
rejected — transport policy lives inside the transport implementations. That
alternative has proven adequate, so the proposal is closed rather than carried
forward. The original 2026-05-21 proposal is preserved verbatim below the line
for historical context.

### Settled state (verified 2026-07-15; transport facts re-verified 2026-07-20)

- **No session type exists.** There is no `OdooSession` (or equivalent) anywhere
  in `src/odoo_sdk`. The only `session`-named code is the unrelated
  `sessionization` ETL package, which has nothing to do with transport.
- **Transport is the pluggable seam, and it owns policy.** `OdooExecutor` (an
  `ABC` in `transport/executor.py`) is the pluggable transport contract. Two
  concrete transports subclass it and each carry their own policy:
  - `OdooRpcExecutor` (`transport/rpc.py`) — the default synchronous XML-RPC
    transport.
  - `OdooJson2Executor` (`transport/json2.py`) — the JSON-2 HTTP transport.
- **Auth policy lives in transport.** `OdooRpcExecutor._authenticate`
  (`transport/rpc.py`) performs lazy `uid` login; `OdooJson2Executor`
  (`transport/json2.py`) sends an `Authorization: Bearer` header. There is no
  shared auth policy above them.
- **Timeout *mechanism* lives in transport; the default *value* is central.**
  The default is defined once as `DEFAULT_TIMEOUT_SECONDS` in `state/config.py`
  and re-exported by both transports as `DEFAULT_REQUEST_TIMEOUT_SECONDS`
  (`transport/rpc.py`, `transport/json2.py`) rather than redefined per transport.
  Enforcing the timeout is still transport-shaped: the XML-RPC side implements
  `_TimeoutTransport` / `_SafeTimeoutTransport` (`transport/rpc.py`) to bound
  socket waits, while JSON-2 passes the timeout to its HTTP call. Sharing one
  scalar default does not amount to a policy layer, so this does not change the
  rejection below.
- **Error mapping lives in transport.** XML-RPC faults are mapped by
  `_mapped_call` (`transport/rpc.py`) via `transport/_fault_mapping.py`; JSON-2
  HTTP errors are mapped by `map_http_error` via
  `transport/_http_error_mapping.py`.
- **The error taxonomy the proposal predicted was actually built — inside
  transport.** `transport/errors.py` defines `OdooError(RuntimeError)` and a
  small taxonomy of subtypes (`OdooAuthenticationError`, `OdooAccessError`,
  `OdooValidationError`, `OdooMissingRecordError`, `OdooTransportError`,
  `OdooServerError`). The proposal listed "error handling will need a small
  taxonomy instead of generic `RuntimeError`" as a *negative consequence of
  adopting a session*; that taxonomy exists today, proving transport can own the
  concern adequately without a session layer above it.

### Why the session layer is rejected

- Two transports each owning their own policy has proven adequate for the
  current public surface. The concerns the session layer was meant to unify
  (auth, timeout, error mapping) are genuinely transport-specific: XML-RPC `uid`
  login and JSON-2 bearer auth do not share meaningful policy, and each
  transport's timeout and fault-to-exception mapping is protocol-shaped.
- The observability motivations (logging, tracing, secret redaction) never
  materialized as real requirements, so the "single integration point" argument
  never had to be paid for.
- Introducing the layer now would be speculative abstraction against no live
  requirement. The honest position is to keep transport as the policy owner and
  reopen the question only when a concrete need lands (see the retry decision
  and reopening trigger below).

### Explicit decision on retry policy

Retry / backoff policy is **deliberately not implemented** and is a non-goal at
the current scope. Re-confirmed 2026-07-20: no retry, backoff, or attempt-loop
logic exists anywhere in `src/odoo_sdk`. Every mention of "retry" in the package
is prose, not mechanism — the load-bearing one is a transport docstring that
points retry responsibility *outward* to the caller: `transport/rpc.py` notes
that a failed login is not cached so callers "may retry after correcting their
credentials". That describes *caller-level* retry, not SDK-owned retry.

This is a decision, not an oversight: callers that need resilience wrap their own
calls, and the transport error taxonomy (`OdooTransportError` distinct from
server-side faults) is specifically designed to let a caller tell transient
connectivity failures apart from business faults for exactly that purpose.

### Reopening trigger

If a first-class retry requirement lands — the obvious candidate being
XML-RPC over flaky networks, where per-call backoff would materially improve
reliability — that is the moment to reconsider a thin policy boundary rather than
bolting a retry loop into one transport by default. At that point:

- Prefer introducing the boundary as a fresh, superseding ADR rather than
  reviving this one, so the concrete requirement is recorded alongside the
  design.
- Keep any such layer thin: it should own cross-transport *policy* (retry,
  backoff, redaction) while transports stay mechanism-only. It should not absorb
  the protocol-shaped auth, timeout, and error-mapping mechanics that correctly
  live in each transport today.

---

*Everything below is the original 2026-05-21 proposal, retained unchanged for
historical context. It was not adopted; see the Resolution above.*

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
