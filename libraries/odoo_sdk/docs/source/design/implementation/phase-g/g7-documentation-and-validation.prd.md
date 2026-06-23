# Feature Name

Phase G Documentation and Validation

# Goal

## Problem

The optional Pydantic layer is only trustworthy if it is tested in both Pydantic-present and Pydantic-absent environments, and the documentation clearly explains the install path and resolution behavior.

## Solution

Update architecture docs, add examples, run the full test suite in both configurations, and run live smoke tests.

# Requirements

## Functional Requirements

- An `examples/` script demonstrating `recordset.read_typed()` for `res.partner`.
- An `examples/` script demonstrating consumer `TypeRegistry` plugin registration for a custom model.
- `docs/odoo-sdk-architecture-plan.md` updated with Phase G boundary and achievement summary.
- `src/odoo_sdk/__init__.py` exports `OdooBaseModel`, `OdooField`, `TypeRegistry`.
- Full test suite passes with Pydantic installed.
- Full test suite passes with Pydantic uninstalled (all Phase G surfaces degrade gracefully).
- Live smoke test: `env['res.partner'].search_read([], ['name', 'email'], limit=1)` via `read_typed()` returns a `ResPartner` instance with correct fields.

## Non-Functional Requirements

- Examples must run with `uv run python examples/<script>.py`.

# Acceptance Criteria

- [ ] Typed model examples exist in `examples/`.
- [ ] `OdooBaseModel`, `OdooField`, `TypeRegistry` are exported from `src/odoo_sdk/__init__.py`.
- [ ] `docs/odoo-sdk-architecture-plan.md` updated.
- [ ] Full test suite passes with and without Pydantic.
- [ ] Live smoke test confirms `ResPartner` is returned from `read_typed()`.
- [ ] All Phase G checklist items marked done.

# Out of Scope

- CI setup for dual Pydantic/no-Pydantic matrix.
- Package publishing.
