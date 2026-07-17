# Feature Name

OdooClient Integration

> **Historical / superseded (2026-07).** No `OdooClient.mcp_server()` factory
> shipped. `OdooMCPServer` is constructed from a command `Registry`, lazily
> importable as `odoo_sdk.OdooMCPServer`, and launched via the `odoo-mcp` entry
> point. The implemented contract is
> [ADR-004 — MCP wraps the command registry](../../architecture/ADR-004-mcp-wraps-the-command-registry.md).

# Goal

## Problem

Without a factory method on `OdooClient`, consumers must construct `OdooMCPServer` directly and manage the `client` reference themselves. This exposes implementation details and makes the embedding pattern fragile.

## Solution

Add `OdooClient.mcp_server(context=None) -> OdooMCPServer` as a factory method.

# Requirements

## Functional Requirements

- `OdooClient.mcp_server(context: dict | None = None) -> OdooMCPServer` — instantiates and returns an `OdooMCPServer` backed by the current client.
- `context` is forwarded to the `OdooMCPServer` constructor and applied to all tool call recordset operations via `env.with_context(context)`.
- If `mcp` is not installed, `mcp_server()` raises `ImportError` with the install message.
- The method is idempotent with respect to the client state: calling it multiple times returns new `OdooMCPServer` instances that share the same executor.

## Non-Functional Requirements

- The client's base env is not mutated by the context parameter.

# Acceptance Criteria

- [ ] `client.mcp_server()` returns an `OdooMCPServer` instance.
- [ ] `client.mcp_server(context={'lang': 'fr_FR'})` propagates `lang='fr_FR'` to tool call recordsets.
- [ ] Without `mcp` installed: `client.mcp_server()` raises `ImportError`.
- [ ] `client.mcp_server().run` is callable.
- [ ] Unit tests cover factory, context propagation, and absent-mcp error.

# Out of Scope

- Singleton behavior (each call returns a new server instance).
- Context merging with the client's existing env context.
