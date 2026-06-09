# Feature Name

Phase E Guardrails

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

- [ ] `docs/implementation/phase-e/phase-e-json2-transport-contract.md` exists and is reviewed.
- [ ] All E1–E6 PRD authors confirm the contract is sufficient to evaluate their tasks.
- [ ] No new external dependencies are introduced in Phase E.

# Out of Scope

- Implementation of any Phase E component.
