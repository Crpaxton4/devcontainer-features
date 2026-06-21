# Feature Name

Phase H Guardrails

# Goal

## Problem

Phase H introduces a new optional dependency (`mcp`), async/sync bridging, and a new package structure. Without a contract first, the boundary between the MCP layer and the existing SDK may blur, and deferred work (CLI, WebSocket) may leak in.

## Solution

Adopt the Phase H MCP contract before any H1–H5 implementation begins.

# Requirements

## Functional Requirements

- The Phase H contract document must exist and be adopted before H1 begins.
- The contract must confirm `mcp` as an optional dependency.
- The contract must confirm embedded library mode only (no CLI).
- The contract must confirm the resource URI scheme and tool list.
- The contract must confirm the async/sync bridging strategy.
- The contract must confirm context propagation behavior.

## Non-Functional Requirements

- The contract must be readable by an implementer without prior planning context.

# Acceptance Criteria

- [ ] `docs/implementation/phase-h/phase-h-mcp-contract.md` exists and is reviewed.
- [ ] H1–H5 PRD authors confirm the contract is sufficient.
- [ ] No CLI entry point is created in Phase H.
- [ ] No WebSocket or SSE transport is added in Phase H.

# Out of Scope

- Implementation of any Phase H component.
