# Feature Name

Phase H Documentation and Validation

> **Historical / superseded (2026-07).** The validation targets here (`odoo://`
> resources, the `search_read` tool, a `client.mcp_server().run()` example) do not
> match what shipped: command-registry atomic and composition tools plus prompts.
> `OdooMCPServer` is exported (lazily) and runnable via the `odoo-mcp` entry point.
> The implemented contract is
> [ADR-004 — MCP wraps the command registry](../../architecture/ADR-004-mcp-wraps-the-command-registry.md).

# Goal

## Problem

The embedded MCP server is only trustworthy if it is validated against a live Odoo instance with an actual MCP client and the embedding pattern is clearly documented.

## Solution

Update architecture docs, add an example script, export `OdooMCPServer`, and run live MCP validation.

# Requirements

## Functional Requirements

- An `examples/` script showing how to embed `OdooMCPServer` in a Python entrypoint (minimal `if __name__ == '__main__': client.mcp_server().run()` pattern).
- `docs/odoo-sdk-architecture-plan.md` updated with Phase H boundary, MCP usage pattern, and resource/tool list.
- `src/odoo_sdk/__init__.py` exports `OdooMCPServer`.
- Full test suite passes with no regressions.
- Live validation: start the MCP server, connect an MCP client (e.g., using the `mcp` Python SDK's test client), call `odoo://models` resource and the `search_read` tool; confirm correct results.
- All Phase H checklist items marked done.

## Non-Functional Requirements

- Example must run with `uv run python examples/<script>.py`.
- The example must include a comment explaining the stdio embedding pattern.

# Acceptance Criteria

- [ ] MCP embedding example exists in `examples/`.
- [ ] `OdooMCPServer` is exported from `src/odoo_sdk/__init__.py`.
- [ ] `docs/odoo-sdk-architecture-plan.md` updated.
- [ ] Full test suite passes with and without the `mcp` package.
- [ ] Live validation succeeds: resource and tool calls return correct Odoo data.
- [ ] All Phase H checklist items marked done.

# Out of Scope

- CLI server packaging.
- Package publishing.
