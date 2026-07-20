# Feature Name

Phase F Documentation and Validation

> **Status: never implemented (2026-07 audit).** No part of Phase F shipped. There is no `OdooModelRegistry`, `ModelSchema`, or `FieldSchema` anywhere in `src/odoo_sdk/`, and no `ir.model` / `ir.model.fields` reflection path. The only metadata layer that shipped is the Phase B `MetadataCache` over `fields_get` (`src/odoo_sdk/env/metadata_cache.py`), which this phase was meant to sit beside. `OdooEnv`, which every Phase F surface below hangs off, was itself removed in PR #161. Retained as a record of the original Phase F plan.

# Goal

## Problem

Schema reflection is only useful if the output can be verified against a live Odoo instance and the public API surface is clearly documented.

## Solution

Update architecture docs, add example scripts, export the new types, and run both unit and live smoke tests.

# Requirements

## Functional Requirements

- An `examples/` script demonstrating `env.get_model_schema('res.partner')` and iterating its fields.
- An `examples/` script demonstrating `registry.discover(['res.partner', 'res.users'])`.
- `docs/odoo-sdk-architecture-plan.md` updated with Phase F boundary and achievement summary.
- `src/odoo_sdk/__init__.py` exports `OdooModelRegistry`, `ModelSchema`, `FieldSchema`.
- All Phase A–E unit tests pass unchanged.
- Live smoke test validates schema against a known Odoo instance: confirm `res.partner` has a `name` field of type `char`.

## Non-Functional Requirements

- Examples must run with `uv run python examples/<script>.py`.

# Acceptance Criteria

- [ ] Schema examples exist in `examples/`.
- [ ] `OdooModelRegistry`, `ModelSchema`, `FieldSchema` are exported from `src/odoo_sdk/__init__.py`.
- [ ] `docs/odoo-sdk-architecture-plan.md` updated.
- [ ] Full test suite passes with no regressions.
- [ ] Live smoke test confirms `res.partner.name` is `char`.
- [ ] All Phase F checklist items marked done.

# Out of Scope

- CI or release automation.
- Package publishing.
