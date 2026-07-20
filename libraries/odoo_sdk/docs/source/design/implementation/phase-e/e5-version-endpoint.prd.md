# Feature Name

Server Version Endpoint

> **Status: never implemented (2026-07 audit).** `OdooClient` has no `server_version()` method and no `server_version_string()` property (`src/odoo_sdk/client/client.py`), and nothing in the source calls `/web/version` or the XML-RPC `common.version()` endpoint. This gap also blocked Phase F's version fingerprint and Phase G's version-aware field stripping, neither of which shipped. Retained as a record of the original Phase E plan.

# Goal

## Problem

XML-RPC used `common.version()` for version detection. JSON-2 uses `GET /web/version`. Phase F (reflection) and Phase G (typed model version compatibility) both need a transport-agnostic `server_version()` call. Currently there is no unified version accessor on `OdooClient`.

## Solution

Add `OdooClient.server_version() -> dict` that works for both XML-RPC and JSON-2 transports and returns a normalized version dict.

# Requirements

## Functional Requirements

- `OdooClient.server_version() -> dict` returns a dict with at minimum: `version` (string, e.g., `'19.0'`) and `version_info` (list of ints).
- For XML-RPC: delegates to `xmlrpc/2/common` `version()` call.
- For JSON-2: issues `GET {url}/web/version` using `urllib`; the endpoint does not require authentication.
- Both paths must return a dict with the same normalized key names.
- The method must work before any model calls are made (does not require a prior `execute` call).
- A `server_version_string() -> str` convenience property returns just the version string (e.g., `'19.0'`).

## Non-Functional Requirements

- The method is synchronous.
- The result may be cached for the lifetime of the client instance since the Odoo version does not change at runtime.

# Acceptance Criteria

- [ ] `client.server_version()` returns a dict with `version` and `version_info` keys for both transport types.
- [ ] `client.server_version_string()` returns a string like `'19.0'`.
- [ ] The method works before any authenticated call is made.
- [ ] The method works for XML-RPC and JSON-2 clients.
- [ ] Unit tests cover both transport paths with mocked responses.

# Out of Scope

- Parsing minor patch versions beyond major.minor.
- Using the version to gate method availability at the executor level.
