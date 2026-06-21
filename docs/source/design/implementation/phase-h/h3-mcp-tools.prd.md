# Feature Name

MCP Tools

# Goal

## Problem

Without tools, the MCP server is read-only and useful only for schema discovery. LLM agents need to search, read, write, and delete records through structured tool calls.

## Solution

Implement 9 MCP tool handlers that wrap the Phase D recordset public surface.

# Requirements

## Functional Requirements

- `search(model: str, domain: list, fields: list[str] | None, limit: int | None, offset: int | None, order: str | None) -> list[int]` — wraps `OdooRecordset.search`.
- `read(model: str, ids: list[int], fields: list[str] | None) -> list[dict]` — wraps `OdooRecordset.read`.
- `search_read(model: str, domain: list, fields: list[str] | None, limit: int | None, offset: int | None, order: str | None) -> list[dict]` — wraps `OdooRecordset.search_read`.
- `create(model: str, values: dict) -> int` — wraps `OdooRecordset.create`; returns new record id.
- `write(model: str, ids: list[int], values: dict) -> bool` — wraps `OdooRecordset.write`.
- `unlink(model: str, ids: list[int]) -> bool` — wraps `OdooRecordset.unlink`.
- `name_search(model: str, name: str, domain: list | None, limit: int | None) -> list[tuple[int, str]]` — wraps `OdooRecordset.name_search`.
- `read_group(model: str, domain: list, fields: list[str], groupby: list[str], limit: int | None) -> list[dict]` — wraps `OdooRecordset._read_group`.
- `fields_get(model: str, attributes: list[str] | None) -> dict` — wraps `OdooRecordset.fields_get`.
- All tool descriptions include: plain-English purpose, parameter types, and at least one example value.
- Errors (e.g., unknown model, invalid domain) are returned as structured JSON error dicts, not unhandled exceptions.

## Non-Functional Requirements

- Domain parameters are accepted as JSON arrays (same format as Odoo's Python API).
- All tools are synchronous SDK calls inside MCP async handlers.

# Acceptance Criteria

- [ ] `search_read('res.partner', [['is_company', '=', True]], ['name'], 5)` returns up to 5 partner dicts.
- [ ] `create('res.partner', {'name': 'Test'})` returns an integer id.
- [ ] `write('res.partner', [1], {'email': 'x@y.com'})` returns `True`.
- [ ] `unlink('res.partner', [9999999])` raises a structured error (record not found).
- [ ] All 9 tools have descriptions.
- [ ] Unit tests with mocked client for each tool and error cases.

# Out of Scope

- Additional tools beyond the 9 listed.
- MCP prompts.
