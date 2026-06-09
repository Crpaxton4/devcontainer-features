# Feature Name

Phase D Guardrails

# Goal

## Problem

Phase D adds a significant number of methods and operators across several core abstractions. Without an explicit contract committed before implementation starts, individual PRDs may drift in scope, duplicate logic across compatibility layers, or import decisions that belong in later phases.

## Solution

Define and adopt the Phase D ORM completeness contract before any D1–D7 implementation work begins. The contract captures every cross-cutting decision — sudo exclusion, client-side operation semantics, `_read_group` canonicalization, and synchronous-only execution — so each later PRD can reference one document instead of resolving the same questions independently.

# Requirements

## Functional Requirements

- The Phase D contract document must exist and be adopted before D1 begins.
- The contract must explicitly list preserved public surfaces and their Phase D status.
- The contract must document the explicit exclusion of `sudo()` with rationale.
- The contract must document the client-side semantics of functional operations.
- The contract must define `DomainExpression.AND([])` and `DomainExpression.OR([])` edge case behavior.
- The contract must list all work explicitly deferred to Phases E–H.

## Non-Functional Requirements

- The contract must be readable by an implementer who has not participated in the planning conversation.
- The contract must not contain implementation details — only boundary decisions and guardrails.

# Acceptance Criteria

- [ ] `docs/implementation/phase-d/phase-d-orm-completeness-contract.md` exists and is reviewed.
- [ ] `sudo()` exclusion is documented with rationale.
- [ ] All preserved public surfaces are listed with their Phase D status.
- [ ] Deferred work is listed explicitly.
- [ ] D1–D7 PRD authors confirm the contract is sufficient to evaluate their tasks.

# Out of Scope

- Implementation of any Phase D method.
- Changes to existing public surfaces.
