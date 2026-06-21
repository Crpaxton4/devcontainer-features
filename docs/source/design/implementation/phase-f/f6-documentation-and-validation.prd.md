# Feature Name

Phase F Documentation and Validation

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
