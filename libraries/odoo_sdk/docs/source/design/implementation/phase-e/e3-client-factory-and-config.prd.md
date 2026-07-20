# Feature Name

Client Factory Methods and Configuration Extension

> **Status: accurate, with one addition (2026-07 audit).** `from_xml_rpc`, `from_json2`, the preserved constructor, and the `transport` / `api_key` settings all shipped (`src/odoo_sdk/client/client.py`, `src/odoo_sdk/state/config.py`). A third factory was added afterwards and is not listed below: `OdooClient.from_config(config: LocalConfig)`, which is the path the CLI and MCP entry points use so settings are resolved once and injected.

# Goal

## Problem

The current `OdooClient` constructor accepts XML-RPC credentials only, and `OdooConnectionSettings` has no concept of API keys or transport selection. Adding JSON-2 requires explicit factory methods and config extension without breaking any existing consumer.

## Solution

Add `OdooClient.from_xml_rpc` and `OdooClient.from_json2` named factory methods. Extend `OdooConnectionSettings` with `transport` and `api_key` fields, including environment variable and INI file support.

# Requirements

## Functional Requirements

- `OdooClient.from_xml_rpc(url, db, username, password) -> OdooClient` class method — explicit XML-RPC factory.
- `OdooClient.from_json2(url, db, api_key) -> OdooClient` class method — creates a client backed by `OdooJson2Executor`.
- The existing `OdooClient(url, db, username, password)` constructor is preserved unchanged for backward compatibility.
- `OdooConnectionSettings` gains `transport: Literal['xmlrpc', 'json2'] = 'xmlrpc'`.
- `OdooConnectionSettings` gains `api_key: str | None = None`.
- `OdooConnectionSettings.from_sources()` resolves `api_key` from `ODOO_API_KEY` environment variable.
- `OdooConnectionSettings.from_sources()` resolves `transport` from `ODOO_TRANSPORT` environment variable.
- The INI config file format (`[odoo]` section) gains `api_key` and `transport` keys.

## Non-Functional Requirements

- `api_key` must not appear in `__repr__` or `__str__` of `OdooConnectionSettings`.
- The default transport remains `'xmlrpc'` to avoid any breaking change.

# Acceptance Criteria

- [ ] `OdooClient.from_json2('https://host', 'mydb', 'key')` returns a working `OdooClient`.
- [ ] `OdooClient.from_xml_rpc('https://host', 'mydb', 'admin', 'pass')` returns a working `OdooClient`.
- [ ] Existing `OdooClient('https://host', 'mydb', 'admin', 'pass')` still works.
- [ ] `OdooConnectionSettings` with `transport='json2'` and `api_key='key'` creates a JSON-2 backed client.
- [ ] `ODOO_API_KEY` env var is picked up by `from_sources()`.
- [ ] `api_key` does not appear in `repr(settings)`.
- [ ] Unit tests cover both factory methods and settings resolution.

# Out of Scope

- Validating that the API key is valid at construction time.
- Supporting multiple API keys or key rotation at the settings level.
