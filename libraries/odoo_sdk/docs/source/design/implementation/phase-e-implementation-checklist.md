# Phase E Implementation Checklist

> **Status: partially superseded (2026-07 audit).** E1, E2, and E3 shipped. E4 (API key management) and E5 (version endpoint) never shipped — no `generate_api_key`, `revoke_api_key`, `server_version`, or `server_version_string` exists on `OdooClient`. E6's example scripts and cross-transport smoke test were not written.

## Objective

Introduce the Odoo JSON-2 API (`/json/2`) as an opt-in transport alongside the existing XML-RPC transport. Phase E keeps XML-RPC as the default and adds `OdooJson2Executor` as a drop-in alternative. The XML-RPC API is deprecated in Odoo 19 and scheduled for removal in Odoo 22; Phase E ensures the SDK has a migration path without breaking any existing consumer.

## PRD-Ready Context

### Problem statement

The SDK's only transport is `OdooRpcExecutor`, which uses the deprecated XML-RPC API. Odoo 19 introduced the JSON-2 API (`/json/2`) as the replacement: HTTP POST with bearer token auth, named arguments, and a clean JSON response format. Without a JSON-2 transport, every SDK consumer faces a hard migration deadline when their Odoo version reaches end of life for XML-RPC support. Adding JSON-2 now, while XML-RPC still works, allows consumers to migrate incrementally.

### Desired outcome

- `OdooJson2Executor` exists and implements the `OdooExecutor` interface.
- All HTTP I/O uses `urllib` from the Python standard library — no new dependencies.
- `OdooClient` has explicit factory methods for both transports.
- `OdooConnectionSettings` supports `api_key` and a `transport` selector.
- HTTP errors and JSON error responses map into the existing SDK error hierarchy.
- A `/web/version` call replaces `common.version()` for JSON-2 connections.
- API key management helpers (`generate_api_key`, `revoke_api_key`) exist on `OdooClient`.
- Both transports produce identical results for the same logical operation when tested against a live Odoo instance.

### Non-goals

- No async transport.
- No `requests` or `httpx` dependency.
- No change to the default transport (XML-RPC remains default).
- No runtime model reflection (Phase F).
- No Pydantic validation (Phase G).
- No MCP integration (Phase H).
- No CI or release automation.

### Constraints

- `OdooExecutor` interface must remain unchanged so `OdooJson2Executor` is a drop-in.
- Zero new external dependencies: all HTTP work uses `urllib.request`, `urllib.error`, `http.client`, and `json` from the standard library.
- All operations are synchronous.
- Preserve all Phase A–D public surfaces.

### Success signal

- `OdooClient.from_json2(url, db, api_key)` creates a working client.
- The same `search_read` call produces identical records whether executed via XML-RPC or JSON-2 against the same live instance.
- All Phase A–D unit tests pass unchanged.
- API key management round-trips (generate, use, revoke) work against a live Odoo 19+ instance.

## Execution Order

1. Lock down Phase E boundaries and JSON-2 transport contract.
2. Implement `OdooJson2Executor` with `urllib`-based HTTP.
3. Add HTTP and JSON error mapping for JSON-2 responses.
4. Update `OdooClient` with factory methods and `OdooConnectionSettings` with new fields.
5. Add API key management helpers.
6. Add version endpoint helper.
7. Update docs, examples, and local validation.

## Implementation Checklist

## E0 - Phase Guardrails

Goal
- Define the exact Phase E contract before transport work begins.

Likely touch points
- `docs/implementation/phase-e/phase-e-json2-transport-contract.md`
- `docs/implementation/phase-e-implementation-checklist.md`
- `docs/odoo-sdk-architecture-plan.md`
- `src/odoo_sdk/transport/executor.py`

Checklist
- [ ] Create and adopt a dedicated Phase E JSON-2 transport contract.
- [ ] Confirm that `OdooExecutor` interface is unchanged.
- [ ] Confirm `urllib` as the only HTTP library.
- [ ] Confirm zero new external dependencies.
- [ ] Confirm XML-RPC remains the default transport.
- [ ] Confirm that all Phase D ORM methods work transparently over JSON-2.

Done when
- The contract is reviewed and the E1–E6 PRD authors can validate their tasks against it.

## E1 - OdooJson2Executor

Goal
- Implement the JSON-2 HTTP executor using `urllib` from the Python standard library.

Why this exists
- The JSON-2 API uses HTTP POST with bearer token authentication and named JSON arguments. The XML-RPC `xmlrpc.client` cannot speak this protocol. A separate executor keeps the transport seam clean.

Likely touch points
- New `src/odoo_sdk/transport/json2.py`
- `src/odoo_sdk/transport/executor.py`
- Tests in `tests/test_transport/`

Checklist
- [ ] Implement `OdooJson2Executor(url, db, api_key)` implementing `OdooExecutor`.
- [ ] Construct the POST URL as `{url}/json/2/{model}/{method}`.
- [ ] Set `Authorization: bearer {api_key}` and `Content-Type: application/json` headers.
- [ ] Set `X-Odoo-Database: {db}` header when `db` is provided.
- [ ] Send `ids`, `context`, and all method keyword arguments in the JSON body.
- [ ] Parse the response body as JSON; return the result on 2xx.
- [ ] Raise the appropriate SDK error on 4xx/5xx (see E2).
- [ ] Add unit tests with mocked `urllib.request.urlopen` for success, 401, 403, 404, 422, and 500 responses.

Done when
- `OdooJson2Executor.execute(model, method, args, kwargs)` works for all Phase D ORM methods.

PRD inputs captured by this item
- User-visible behavior change: JSON-2 becomes an available transport option.
- Main technical risk: `urllib` error handling is more verbose than higher-level libraries; error extraction from JSON bodies requires careful parsing.

## E2 - HTTP and JSON Error Mapping

Goal
- Map JSON-2 HTTP error responses and JSON error bodies into the existing SDK error hierarchy.

Why this exists
- JSON-2 errors arrive as HTTP status codes with JSON bodies containing `name`, `message`, `arguments`, and `debug` fields. These must map to the same `OdooError` subclasses that XML-RPC faults map to.

Likely touch points
- `src/odoo_sdk/transport/errors.py`
- `src/odoo_sdk/transport/json2.py`
- Tests in `tests/test_transport/`

Checklist
- [ ] Map HTTP 401 → `OdooAuthenticationError`.
- [ ] Map HTTP 403 → `OdooAccessError`.
- [ ] Map HTTP 404 → `OdooMissingRecordError` when the JSON body indicates a missing record; otherwise `OdooServerError`.
- [ ] Map HTTP 422 → `OdooValidationError`.
- [ ] Map all other 4xx/5xx → `OdooServerError` with the JSON `message` and `debug` fields preserved.
- [ ] Handle responses where the body is not valid JSON (e.g., proxy errors) by raising `OdooTransportError`.
- [ ] Add unit tests for each mapping path.

Done when
- Every JSON-2 error response maps to one unambiguous SDK error class.

PRD inputs captured by this item
- User-visible behavior change: JSON-2 failures are consistent with XML-RPC failures.
- Main technical risk: the Odoo JSON `name` field (Python exception class name) may be more precise than HTTP status codes; use it to disambiguate where possible.

## E3 - Client Factory Methods and Config Extension

Goal
- Add `OdooClient.from_xml_rpc` and `OdooClient.from_json2` explicit factory methods and extend `OdooConnectionSettings` with `transport` and `api_key` fields.

Why this exists
- The current `OdooClient` constructor is overloaded with connection parameters. Named factories make transport intent explicit and allow `OdooConnectionSettings` to be the single configuration object for both transports.

Likely touch points
- `src/odoo_sdk/client/client.py`
- `src/odoo_sdk/config/settings.py`
- Tests in `tests/test_client/`

Checklist
- [ ] Add `OdooClient.from_xml_rpc(url, db, username, password) -> OdooClient` class method.
- [ ] Add `OdooClient.from_json2(url, db, api_key) -> OdooClient` class method.
- [ ] Preserve the existing `OdooClient(url, db, username, password)` constructor for backward compatibility.
- [ ] Add `transport: Literal['xmlrpc', 'json2'] = 'xmlrpc'` field to `OdooConnectionSettings`.
- [ ] Add `api_key: str | None = None` field to `OdooConnectionSettings`.
- [ ] Add `ODOO_API_KEY` environment variable support to `OdooConnectionSettings.from_sources()`.
- [ ] Add `api_key` support to the INI config file format.
- [ ] Add unit tests for factory method construction and settings resolution.

Done when
- Consumers can choose transport explicitly via a factory method or via `OdooConnectionSettings`.

PRD inputs captured by this item
- User-visible behavior change: transport selection is explicit and self-documenting.
- Main technical risk: backward-compatibility of the existing constructor when new fields are added.

## E4 - API Key Management Helpers

Goal
- Add `generate_api_key` and `revoke_api_key` methods to `OdooClient` for programmatic key lifecycle management.

Why this exists
- The JSON-2 API provides `res.users.apikeys.generate()` and `res.users.apikeys.revoke()` methods. Wrapping these in the client makes key rotation and automation easier without requiring consumers to call `execute` directly.

Likely touch points
- `src/odoo_sdk/client/client.py`
- Tests in `tests/test_client/`

Checklist
- [ ] Add `OdooClient.generate_api_key(scope, name, expiration_date) -> str` — calls `res.users.apikeys/generate` and returns the new key string.
- [ ] Add `OdooClient.revoke_api_key(key) -> None` — calls `res.users.apikeys/revoke`.
- [ ] Both methods must only be available when the client uses `OdooJson2Executor`; raise `NotImplementedError` on XML-RPC clients.
- [ ] Document key rotation best practices in the method docstrings.
- [ ] Add unit tests with mocked executor responses.

Done when
- Programmatic API key rotation works through the `OdooClient` interface.

PRD inputs captured by this item
- User-visible behavior change: key lifecycle management is first-class SDK behavior.
- Main technical risk: the generate endpoint requires an existing valid key for authentication; document the bootstrapping requirement.

## E5 - Version Endpoint

Goal
- Add a `version()` method to `OdooClient` that works for both XML-RPC and JSON-2 transports.

Why this exists
- XML-RPC used `common.version()`. JSON-2 uses `GET /web/version`. Version information is needed for Phase F reflection (version fingerprinting) and Phase G typed model version compatibility.

Likely touch points
- `src/odoo_sdk/client/client.py`
- `src/odoo_sdk/transport/json2.py`
- `src/odoo_sdk/transport/rpc.py`
- Tests in `tests/test_client/`

Checklist
- [ ] Add `OdooClient.server_version() -> dict` — returns version info dict for both transports.
- [ ] For XML-RPC: delegate to `common.version()`.
- [ ] For JSON-2: issue `GET /web/version` using `urllib` and parse the JSON response.
- [ ] Return a dict with at least `version` (string) and `version_info` (list) keys normalized across both transports.
- [ ] Add unit tests for both transport paths.

Done when
- `client.server_version()` returns consistent version info regardless of which transport is active.

PRD inputs captured by this item
- User-visible behavior change: version detection is transport-agnostic.
- Main technical risk: JSON-2 `/web/version` does not require auth; XML-RPC `common.version()` does not either; both must work before authentication.

## E6 - Documentation and Validation

Goal
- Update all phase documentation and examples; validate both transports against a live Odoo instance.

Likely touch points
- `docs/odoo-sdk-architecture-plan.md`
- `examples/`
- `src/odoo_sdk/__init__.py`

Checklist
- [ ] Add an `examples/` script demonstrating JSON-2 transport with `OdooClient.from_json2`.
- [ ] Add an `examples/` script demonstrating API key generation and revocation.
- [ ] Update `docs/odoo-sdk-architecture-plan.md` with Phase E boundary and achievement summary.
- [ ] Update `src/odoo_sdk/__init__.py` to export `OdooJson2Executor`.
- [ ] Run full test suite; confirm no Phase A–D regressions.
- [ ] Run live integration smoke test comparing XML-RPC and JSON-2 results for the same query.
- [ ] Mark all Phase E checklist items done.

Done when
- Both transports are validated against a live Odoo instance and produce identical results.

## Detailed PRDs

```{toctree}
:maxdepth: 1
:glob:

phase-e/*
```
