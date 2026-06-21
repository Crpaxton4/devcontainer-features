# Phase H Implementation Checklist

## Objective

Embed an MCP (Model Context Protocol) server inside the SDK so AI coding assistants and LLM agents can interact with a connected Odoo instance through standardized MCP resources and tools. The MCP layer is accessed via `client.mcp_server()` factory, inherits the existing authenticated connection, and exposes schema discovery, record reads, and write operations through MCP-native interfaces.

## PRD-Ready Context

### Problem statement

AI assistants and LLM agents working with Odoo data must currently call raw SDK methods programmatically. There is no MCP server that exposes the Odoo data model in a way that LLMs can discover and navigate schema, enumerate records, and perform structured operations using MCP's resource + tool protocol.

### Desired outcome

- `OdooMCPServer` is accessible via `client.mcp_server()`.
- The server inherits the existing executor (no separate authentication).
- MCP Resources: `odoo://models` (installed model list), `odoo://model/{name}/schema` (field definitions), `odoo://model/{name}/records/{id}` (single record).
- MCP Tools: `search`, `read`, `search_read`, `create`, `write`, `unlink`, `name_search`, `read_group`, `fields_get`.
- AI-friendly design: all tool descriptions include field type info and human labels; paginated results; schema self-documentation via resources.
- The `lang` and `active_test` context keys are configurable per MCP session.

### Non-goals

- Standalone CLI MCP server process.
- WebSocket or SSE transport (uses stdio MCP transport only).
- Async execution.
- CI or release automation.

### Constraints

- Uses the `mcp` Python library as a new optional dependency.
- Embedded library mode only: `client.mcp_server()` factory, not a CLI.
- All operations are synchronous (the MCP library handles async dispatch internally; SDK calls remain sync).
- No new transport or authentication beyond what Phase E established.

### Success signal

- An LLM agent can call `odoo://models` to enumerate available models.
- An LLM agent can call `odoo://model/res.partner/schema` to get field definitions.
- An LLM agent can call the `search_read` tool to retrieve partner records.
- The `search` tool accepts a domain expressed as a JSON array.
- Phase A–G test suite passes with no regressions.

## Execution Order

1. Lock down Phase H boundaries and MCP contract.
2. Implement `OdooMCPServer` core class and stdio transport integration.
3. Implement MCP Resources.
4. Implement MCP Tools.
5. Wire `client.mcp_server()` factory into `OdooClient`.
6. Update docs, examples, and validation.

## Implementation Checklist

## H0 - Phase Guardrails

Goal
- Define the exact Phase H contract before any MCP work begins.

Likely touch points
- `docs/implementation/phase-h/phase-h-mcp-contract.md`
- `docs/implementation/phase-h-implementation-checklist.md`

Checklist
- [ ] Create and adopt a dedicated Phase H MCP contract.
- [ ] Confirm `mcp` Python library as optional dependency.
- [ ] Confirm embedded library mode only (no CLI process).
- [ ] Confirm synchronous SDK calls within MCP tool handlers.
- [ ] Confirm resource URI scheme: `odoo://`.
- [ ] Confirm MCP tool list.
- [ ] Confirm deferred work (WebSocket, SSE, async, CLI).

Done when
- H1–H5 PRD authors can validate their tasks against the contract.

## H1 - OdooMCPServer Core

Goal
- Implement the `OdooMCPServer` class with stdio transport integration.

Why this exists
- The core class is the entry point for the MCP layer. It holds a reference to the existing executor, initializes the MCP server, and provides the runtime handle for registering resources and tools.

Likely touch points
- New `src/odoo_sdk/mcp/__init__.py`
- New `src/odoo_sdk/mcp/server.py`
- `pyproject.toml` (`mcp` optional dependency under `[mcp]` extra)
- Tests in `tests/test_mcp/`

Checklist
- [ ] `OdooMCPServer(client: OdooClient)` constructor.
- [ ] `OdooMCPServer.run()` — starts the MCP server using `mcp.server.stdio.stdio_server` context manager; blocks until the client disconnects.
- [ ] The server name is `'odoo-sdk-mcp'`.
- [ ] Importing `OdooMCPServer` when `mcp` is not installed raises `ImportError` with a clear install message.
- [ ] `pyproject.toml` gains `[project.optional-dependencies] mcp = ["mcp>=1.0"]`.
- [ ] Skeleton unit test confirms the class instantiates and `run()` exists.

Done when
- `OdooMCPServer` is importable and `run()` starts the server.

PRD inputs captured by this item
- User-visible behavior change: MCP server is accessible via the SDK.
- Main technical risk: the `mcp` library's async internals must be wrapped correctly to keep SDK calls synchronous.

## H2 - MCP Resources

Goal
- Implement MCP Resources for model enumeration and schema introspection.

Why this exists
- Resources allow an LLM to discover what Odoo models and fields are available before making tool calls. This is the "schema self-documentation" layer that makes the MCP server AI-friendly.

Likely touch points
- New `src/odoo_sdk/mcp/resources.py`
- `src/odoo_sdk/mcp/server.py` (register resources)
- Tests in `tests/test_mcp/`

Checklist
- [ ] Resource `odoo://models` — returns a JSON list of all installed model names and their descriptions (uses Phase F `registry.discover()` or a direct `ir.model` query).
- [ ] Resource `odoo://model/{name}/schema` — returns a JSON object with model name, description, and all field definitions from `ModelSchema`; field entries include `string` (human label), `ttype`, `required`, `readonly`, `help`.
- [ ] Resource `odoo://model/{name}/records/{id}` — returns a JSON object for a single record using `read([id])`.
- [ ] Resources are registered on the MCP server at construction time.
- [ ] Resource not found (unknown model or record) returns a structured JSON error, not an exception.
- [ ] Unit tests with mocked client for each resource URI pattern.

Done when
- An MCP client can enumerate models and introspect field schemas.

PRD inputs captured by this item
- User-visible behavior change: LLM agents can self-document available data shapes.
- Main technical risk: `odoo://model/{name}/schema` must gracefully handle models not in Phase F cache by falling back to a direct `fields_get` call.

## H3 - MCP Tools

Goal
- Implement MCP Tools for data operations.

Why this exists
- Tools are the action layer. Without them, the LLM can only read schema — it cannot search, create, update, or delete records.

Likely touch points
- New `src/odoo_sdk/mcp/tools.py`
- `src/odoo_sdk/mcp/server.py` (register tools)
- Tests in `tests/test_mcp/`

Checklist
- [ ] Tool `search(model, domain, fields, limit, offset, order)` — wraps `OdooRecordset.search`; domain is a JSON array.
- [ ] Tool `read(model, ids, fields)` — wraps `OdooRecordset.read`.
- [ ] Tool `search_read(model, domain, fields, limit, offset, order)` — wraps `OdooRecordset.search_read`.
- [ ] Tool `create(model, values)` — wraps `OdooRecordset.create`; `values` is a JSON object.
- [ ] Tool `write(model, ids, values)` — wraps `OdooRecordset.write`.
- [ ] Tool `unlink(model, ids)` — wraps `OdooRecordset.unlink`.
- [ ] Tool `name_search(model, name, domain, limit)` — wraps `OdooRecordset.name_search`.
- [ ] Tool `read_group(model, domain, fields, groupby, limit)` — wraps `OdooRecordset._read_group`.
- [ ] Tool `fields_get(model, attributes)` — wraps `OdooRecordset.fields_get`.
- [ ] All tool descriptions include: purpose, parameter types, and example values.
- [ ] Each tool result is a JSON-serializable dict.
- [ ] Errors are returned as structured JSON (not unhandled exceptions).
- [ ] Unit tests with mocked client for each tool.

Done when
- An LLM agent can perform a full CRUD cycle on any installed Odoo model.

PRD inputs captured by this item
- User-visible behavior change: LLM agents can perform data operations without custom code.
- Main technical risk: JSON domain parsing must handle both `[[]]` (empty domain) and nested Odoo domain conditions.

## H4 - OdooClient Integration

Goal
- Wire `client.mcp_server()` factory into `OdooClient` and document the embedding pattern.

Why this exists
- Without a factory method on `OdooClient`, consumers must construct `OdooMCPServer` directly, which breaks encapsulation and requires them to know about the `mcp` package internals.

Likely touch points
- `src/odoo_sdk/client/client.py`
- Tests in `tests/test_mcp/`

Checklist
- [ ] `OdooClient.mcp_server() -> OdooMCPServer` — factory method that instantiates and returns an `OdooMCPServer` using the client's current executor.
- [ ] If `mcp` is not installed, `mcp_server()` raises `ImportError` with install instructions.
- [ ] `context` parameter on `mcp_server(context: dict | None = None)` sets default MCP session context (e.g., `{'lang': 'fr_FR', 'active_test': False}`).
- [ ] The `context` is applied to all tool calls made through the server.
- [ ] Unit tests confirm factory returns an `OdooMCPServer` and that context is applied.

Done when
- `client.mcp_server()` is the single entry point for the MCP layer.

PRD inputs captured by this item
- User-visible behavior change: MCP server is accessible from any `OdooClient` instance.
- Main technical risk: the `context` dict must propagate into all recordset operations without mutating the client's base env.

## H5 - Documentation and Validation

Goal
- Update architecture docs, add examples, and validate the MCP server against a live Odoo instance.

Likely touch points
- `docs/odoo-sdk-architecture-plan.md`
- `examples/`
- `src/odoo_sdk/__init__.py`

Checklist
- [ ] Add an example script showing how to embed `OdooMCPServer` in a Python entrypoint.
- [ ] Add documentation to `docs/odoo-sdk-architecture-plan.md` covering Phase H boundary and MCP usage pattern.
- [ ] Export `OdooMCPServer` from `src/odoo_sdk/__init__.py`.
- [ ] Full test suite passes with no regressions.
- [ ] Run live validation: start the embedded MCP server, connect an MCP client, call `odoo://models` and `search_read` tool, confirm correct results.
- [ ] All Phase H checklist items marked done.

Done when
- MCP server validated against a live Odoo instance and documentation is current.

## Detailed PRDs

```{toctree}
:maxdepth: 1
:glob:

phase-h/*
```
