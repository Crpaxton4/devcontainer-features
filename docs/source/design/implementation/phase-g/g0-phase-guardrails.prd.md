# Feature Name

Phase G Guardrails

# Goal

## Problem

Phase G introduces Pydantic as an optional dependency, a new type resolution system, and validation hooks on existing write/create paths. Without a contract first, the boundary between "Pydantic present" and "Pydantic absent" code paths may be inconsistent, and the three-tier resolution order may be implemented differently across G1–G6.

## Solution

Adopt the Phase G type system contract before any G1–G6 implementation begins.

# Requirements

## Functional Requirements

- The Phase G contract document must exist and be adopted before G1 begins.
- The contract must confirm Pydantic as an optional dependency (v2 only).
- The contract must define the three-tier resolution order.
- The contract must confirm `_supported_versions` as an explicit whitelist.
- The contract must confirm validation scope (write/create only).
- The contract must list the initial 12 `base` module models.

## Non-Functional Requirements

- The contract must be readable by an implementer without prior planning context.

# Acceptance Criteria

- [ ] `docs/implementation/phase-g/phase-g-type-system-contract.md` exists and is reviewed.
- [ ] G1–G6 PRD authors confirm the contract is sufficient.
- [ ] No non-`base` module pre-built models are created in Phase G.
- [ ] No async validation is added in Phase G.

# Out of Scope

- Implementation of any Phase G component.
