# Phase H MCP Contract

## Purpose

This contract is the implementation baseline for Phase H.

Its job is to embed an MCP (Model Context Protocol) server inside the SDK so LLM agents can discover and operate on Odoo data using the MCP resource + tool protocol. Every Phase H task is evaluated against this document before it is accepted.

## Preserved Public Surfaces

| Surface | Phase H status | Guardrail |
|---|---|---|
| `OdooClient` | Preserved and extended | Gains `mcp_server()` factory method |
| `OdooEnv` | Preserved | Used by MCP tools via `client.env` |
| `OdooRecordset` | Preserved | MCP tools wrap existing recordset methods |
| `OdooModelRegistry` | Preserved | Used by MCP resources for schema |
| All Phase A–G exports | Preserved | No regressions permitted |

## Responsibility Boundaries

| Abstraction | Owns in Phase H | Does not own |
|---|---|---|
| `OdooMCPServer` | MCP lifecycle, resource/tool registration, stdio transport | Transport authentication, schema generation |
| MCP Resources | Schema introspection, record fetching via URI | Write operations |
| MCP Tools | Data CRUD, search, groupby | Schema definition, resource URIs |
| `OdooClient.mcp_server()` | Factory construction, context propagation | MCP protocol implementation |

## Resolved Phase H Decisions

### Embedded Library Mode Only

`OdooMCPServer` is a Python class, not a CLI command. It is instantiated via `client.mcp_server()` and launched by calling `.run()`, which blocks. There is no `odoo-sdk-mcp` CLI entry point.

### `mcp` Is an Optional Dependency

`pip install odoo_sdk[mcp]`. If `mcp` is not installed, `client.mcp_server()` raises `ImportError`. All SDK functionality works without the `mcp` package.

### Synchronous SDK Calls Within Async MCP Handlers

The `mcp` library uses Python async internally. MCP tool handlers run in an async context, but they invoke SDK methods synchronously using `loop.run_in_executor` or by calling the synchronous SDK methods directly (which is safe as long as they do not block the event loop for extended periods). Explicit guidance on this pattern is documented in the implementation.

### Resource URI Scheme

All MCP resources use the `odoo://` scheme:
- `odoo://models` — list of all installed models
- `odoo://model/{name}/schema` — field definitions for one model
- `odoo://model/{name}/records/{id}` — single record as JSON

### Schema Fallback for Resources

`odoo://model/{name}/schema` uses Phase F `ModelSchema` when available. If Phase F is not set up or the model is not yet cached, the resource falls back to a direct `fields_get` call on the model.

### Context Propagation

`client.mcp_server(context={'lang': 'fr_FR'})` sets a default context dict applied to all tool calls via `recordset.with_context(...)`. This does not mutate the client's base env.

### Tool List Is Fixed

The 9 tools (`search`, `read`, `search_read`, `create`, `write`, `unlink`, `name_search`, `read_group`, `fields_get`) cover the full Phase D recordset public surface. No additional tools are added in Phase H.

## Explicitly Deferred Work

- Standalone CLI MCP server process.
- WebSocket or SSE transport.
- Async SDK execution path.
- MCP prompts (resources and tools only in Phase H).
- CI or release automation.
