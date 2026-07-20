# Feature Name

Phase E Guardrails

> **Status: partially superseded (2026-07 audit).** E1–E3 shipped. E4 (API key management) and E5 (version endpoint) never shipped. The other contract points hold: `urllib` is still the only HTTP library (`src/odoo_sdk/transport/json2.py`), XML-RPC is still the default transport, and the `OdooExecutor` interface is unchanged.

# Goal

## Problem

Phase E introduces a second transport and changes `OdooConnectionSettings` and `OdooClient`. Without a contract committed first, individual PRDs may drift and introduce dependencies, mutate the `OdooExecutor` interface, or change default behavior for existing consumers.

## Solution

Adopt the Phase E JSON-2 transport contract before any E1–E6 implementation begins.

# Requirements

## Functional Requirements

- The Phase E contract document must exist and be adopted before E1 begins.
- The contract must confirm `urllib` as the only HTTP library.
- The contract must confirm XML-RPC remains the default.
- The contract must confirm the `OdooExecutor` interface is unchanged.
- The contract must describe the named-arguments-only constraint for JSON-2.
- The contract must state that API key management is JSON-2 only.

## Non-Functional Requirements

- The contract must be readable by an implementer who has not participated in the planning conversation.

# Acceptance Criteria

- [x] `docs/implementation/phase-e/phase-e-json2-transport-contract.md` exists and is reviewed.
- [x] All E1–E6 PRD authors confirm the contract is sufficient to evaluate their tasks.
- [x] No new external dependencies are introduced in Phase E.

<!-- Contract review completed 2026-06-10.
     E1: stdlib Only + Named Arguments Only + Synchronous Only cover executor scope.
     E2: Error Mapping Priority section covers JSON name-first + HTTP status fallback.
     E3: XML-RPC Remains the Default + API Key Management Is JSON-2 Only cover factory/config scope.
     E4: API Key Management Is JSON-2 Only section covers generate/revoke semantics.
     E5: stdlib Only covers GET /web/version via urllib; OdooExecutor Interface Is Unchanged covers server_version().
     E6: Preserved Public Surfaces table lists all new exports; zero-dependency policy confirmed.
     pyproject.toml [project] dependencies = [] — no external HTTP library introduced. -->

# Out of Scope

- Implementation of any Phase E component.
