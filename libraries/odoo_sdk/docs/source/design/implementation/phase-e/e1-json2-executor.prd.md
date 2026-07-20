# Feature Name

OdooJson2Executor — JSON-2 HTTP Transport

> **Status: accurate, with one addition (2026-07 audit).** `OdooJson2Executor` shipped as described (`src/odoo_sdk/transport/json2.py`). The constructor takes one parameter not listed below — `timeout: float`, defaulted from `odoo_sdk.state.config.DEFAULT_TIMEOUT_SECONDS` so the settings layer and both transports share one number by reference.

# Goal

## Problem

The Odoo JSON-2 API (`/json/2`) uses HTTP POST with bearer token authentication and a JSON body. The existing `OdooRpcExecutor` uses `xmlrpc.client` and cannot speak this protocol. A new executor is needed that implements the same `OdooExecutor` interface while targeting JSON-2.

## Solution

Implement `OdooJson2Executor` using `urllib.request` from the Python standard library. The executor constructs the POST URL, sets headers, serializes the JSON body, sends the request, parses the response, and raises SDK errors on failure.

# Requirements

## Functional Requirements

- `OdooJson2Executor(url: str, db: str | None, api_key: str)` constructor.
- `execute(model, method, args, kwargs) -> Any` — constructs `POST {url}/json/2/{model}/{method}` and returns the parsed response value.
- Sets `Authorization: bearer {api_key}` header on every request.
- Sets `Content-Type: application/json; charset=utf-8` header.
- Sets `X-Odoo-Database: {db}` header when `db` is not `None`.
- The JSON body is an object containing `context` (from kwargs), `ids` (from args[0] if present and the method is not `@api.model`-decorated), and all remaining kwargs as top-level fields.
- On HTTP 2xx: parse the JSON response body and return the value.
- On HTTP 4xx/5xx: parse the JSON error body and raise the appropriate SDK error (see E2).
- On non-JSON responses (e.g., proxy errors): raise `OdooTransportError`.
- Uses `urllib.request.urlopen` with a `urllib.request.Request` object; no third-party HTTP library.

## Non-Functional Requirements

- The executor must be synchronous.
- The executor must not maintain a persistent HTTP connection or session state between calls.
- The executor must not cache any responses.
- `api_key` must not be logged or included in any exception message.

# Acceptance Criteria

- [ ] `OdooJson2Executor` instantiates with `url`, `db`, and `api_key`.
- [ ] A `search` call sends the correct POST URL, headers, and JSON body.
- [ ] A `read` call places `ids` and `fields` correctly in the JSON body.
- [ ] A 200 response returns the parsed JSON value.
- [ ] A 401 response raises `OdooAuthenticationError`.
- [ ] A 500 response raises `OdooServerError` with the server message preserved.
- [ ] A non-JSON 500 response raises `OdooTransportError`.
- [ ] The `api_key` does not appear in any raised exception's message or repr.
- [ ] Unit tests cover success, 401, 403, 404, 422, 500, and non-JSON error cases using mocked `urllib.request.urlopen`.

# Out of Scope

- Persistent connections or HTTP keep-alive.
- Retry or timeout policy (Phase C execution policy hooks).
- Async variant.
