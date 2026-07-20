# Phase E JSON-2 Transport Contract

> **Status: partially superseded (2026-07 audit).** The transport half of this contract shipped: `OdooJson2Executor`, the HTTP error mapping (`src/odoo_sdk/transport/_http_error_mapping.py`), and the `transport` / `api_key` settings. The two JSON-2-only client capabilities it scopes — API key management (E4) and the version endpoint (E5) — never shipped.

## Purpose

This contract is the implementation baseline for Phase E.

Its job is to add the Odoo JSON-2 API (`/json/2`) as an opt-in transport while keeping XML-RPC as the default, maintaining the `OdooExecutor` interface as the single transport seam, and introducing zero new external dependencies.

## Preserved Public Surfaces

| Surface | Phase E status | Guardrail |
|---|---|---|
| `OdooClient` | Preserved and extended | Gains `from_xml_rpc`, `from_json2`, `server_version`, `server_version_string`, `generate_api_key`, `revoke_api_key` |
| `OdooExecutor` | Preserved | Interface is unchanged; `OdooJson2Executor` implements it |
| `OdooRpcExecutor` | Preserved | Remains the default; behavior is unchanged |
| `OdooConnectionSettings` | Preserved and extended | Gains `transport` and `api_key` fields |
| `OdooModel` | Preserved | No changes |
| `OdooQuery` | Preserved | No changes |
| `CommandDispatcher` | Preserved | No changes |

## Responsibility Boundaries

| Abstraction | Owns in Phase E | Does not own |
|---|---|---|
| `OdooJson2Executor` | JSON-2 HTTP POST via `urllib`, bearer token auth, JSON body construction, response parsing, HTTP error mapping | Business logic, metadata caching, field adaptation |
| `OdooClient` | Transport selection via factory methods, API key management | HTTP implementation details |
| `OdooConnectionSettings` | Connection parameter resolution including `api_key` and `transport` | Transport execution |

## Resolved Phase E Decisions

### stdlib Only

All HTTP I/O in `OdooJson2Executor` uses `urllib.request.urlopen`, `urllib.error.HTTPError`, and `urllib.error.URLError` from the Python standard library. No `requests`, `httpx`, or other HTTP library is introduced. This preserves the zero-dependency policy.

### XML-RPC Remains the Default

The existing `OdooClient(url, db, username, password)` constructor and all existing consumer code continue to use `OdooRpcExecutor`. JSON-2 requires an explicit opt-in via `OdooClient.from_json2(url, db, api_key)` or via `OdooConnectionSettings(transport='json2', api_key=...)`.

### OdooExecutor Interface Is Unchanged

`OdooJson2Executor` implements `OdooExecutor` without any changes to the interface. All Phase A–D recordset operations that call `env.execute(...)` work transparently over JSON-2 without modification.

### Named Arguments Only

JSON-2 does not support positional arguments. All method arguments are passed as named keyword arguments in the JSON body. `OdooJson2Executor.execute(model, method, args, kwargs)` ignores the `args` list when calling JSON-2 and maps all arguments through `kwargs`. Methods that require positional arguments (e.g., `read(ids, fields)`) must have their positional args converted to named args in the executor layer.

### Error Mapping Priority

JSON-2 error responses carry both an HTTP status code and a JSON body with a Python exception class name in the `name` field. The executor uses the JSON `name` field as the primary discriminator when available, falling back to HTTP status codes. This matches the precision of the existing XML-RPC fault code mapping.

### API Key Management Is JSON-2 Only

`generate_api_key` and `revoke_api_key` require JSON-2 transport. Calling them on an XML-RPC client raises `NotImplementedError`. The bootstrapping requirement (you need a key to generate a key) is documented in the method docstrings.

### Synchronous Only

All JSON-2 HTTP calls are synchronous. No background threads, connection pools, or async variants are introduced.

## Decision Gates

The table below maps each resolved decision to the Phase E PRD(s) that depend on it. PRD authors can use this to confirm the contract is sufficient before starting implementation.

| Decision | Gates |
|---|---|
| stdlib Only (`urllib`) | E1, E5 |
| XML-RPC Remains the Default | E3 |
| `OdooExecutor` Interface Is Unchanged | E1, E3, E6 |
| Named Arguments Only | E1 |
| Error Mapping Priority (JSON `name` first, HTTP status fallback) | E1, E2 |
| API Key Management Is JSON-2 Only | E3, E4 |
| Synchronous Only | E1, E5 |

## Explicitly Deferred Work

- Runtime model reflection and schema discovery (Phase F)
- Pydantic validation (Phase G)
- MCP integration (Phase H)
- Async transport
- Connection pooling or keep-alive management
- CI or release automation
