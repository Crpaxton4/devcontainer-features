# Feature Name

Phase F Guardrails

> **Status: never implemented (2026-07 audit).** No part of Phase F shipped. There is no `OdooModelRegistry`, `ModelSchema`, or `FieldSchema` anywhere in `src/odoo_sdk/`, and no `ir.model` / `ir.model.fields` reflection path. The only metadata layer that shipped is the Phase B `MetadataCache` over `fields_get` (`src/odoo_sdk/env/metadata_cache.py`), which this phase was meant to sit beside. `OdooEnv`, which every Phase F surface below hangs off, was itself removed in PR #161. Retained as a record of the original Phase F plan.

# Goal

## Problem

Phase F introduces schema reflection, new data structures, and a new cache layer. Without a contract first, the boundary between `MetadataCache` and `OdooModelRegistry` may blur, and deferred Phase G/H work may leak in.

## Solution

Adopt the Phase F reflection contract before any F1–F6 implementation begins.

# Requirements

## Functional Requirements

- The Phase F contract document must exist and be adopted before F1 begins.
- The contract must clarify the boundary between `MetadataCache` and `OdooModelRegistry`.
- The contract must confirm lazy-by-default, synchronous-only discovery.
- The contract must confirm that field validation is deferred to Phase G.
- The contract must confirm registry sharing across derived envs.

## Non-Functional Requirements

- The contract must be readable by an implementer without prior planning context.

# Acceptance Criteria

- [ ] `docs/implementation/phase-f/phase-f-reflection-contract.md` exists and is reviewed.
- [ ] F1–F6 PRD authors confirm the contract is sufficient.
- [ ] No Pydantic or MCP work begins in Phase F.

# Out of Scope

- Implementation of any Phase F component.
