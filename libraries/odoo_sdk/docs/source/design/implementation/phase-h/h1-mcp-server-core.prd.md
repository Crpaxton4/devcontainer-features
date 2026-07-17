# Feature Name

OdooMCPServer Core

> **Historical / superseded (2026-07).** This PRD describes the original Phase H
> server shape, not what shipped. `OdooMCPServer` takes a command `Registry` (not
> an `OdooClient`), is built on FastMCP (not `mcp`), registers no resources, and is
> exposed via the `odoo-mcp` entry point and a lazy `odoo_sdk.OdooMCPServer`
> import — not a `client.mcp_server()` factory. The implemented contract is
> [ADR-004 — MCP wraps the command registry](../../architecture/ADR-004-mcp-wraps-the-command-registry.md).

# Goal

## Problem

There is no entry point for MCP integration in the SDK. Consumers wanting to expose Odoo data to LLM agents must build the entire MCP server themselves using raw SDK calls.

## Solution

Implement `OdooMCPServer` as a class in `src/odoo_sdk/mcp/server.py` with stdio transport, and add `mcp` as an optional dependency.

# Requirements

## Functional Requirements

- `OdooMCPServer(client: OdooClient, context: dict | None = None)` constructor.
- `OdooMCPServer.run()` — starts the MCP server on stdio using the `mcp` library's `Server` and `stdio_server` context; blocks until the MCP client disconnects.
- The MCP server name is `'odoo-sdk-mcp'` and version matches the SDK version from `pyproject.toml`.
- Importing `OdooMCPServer` when `mcp` is not installed raises `ImportError` with message: `"OdooMCPServer requires mcp. Install it with: pip install odoo_sdk[mcp]"`.
- `pyproject.toml` gains `[project.optional-dependencies] mcp = ["mcp>=1.0"]`.
- Resources and tools from H2/H3 are registered in `run()` before the server starts.

## Non-Functional Requirements

- The SDK methods invoked inside MCP tool handlers are called synchronously; the MCP library's async dispatch is handled by the `mcp` library, not by the SDK.

# Acceptance Criteria

- [ ] `OdooMCPServer(client)` instantiates without error.
- [ ] `run()` method exists and calls the `mcp` stdio server.
- [ ] Importing without `mcp` raises `ImportError` with the expected message.
- [ ] `pyproject.toml` has the `mcp` optional extra.
- [ ] Skeleton unit test confirms instantiation.

# Out of Scope

- Resource and tool registration (H2 and H3).
- WebSocket or SSE transport.
