# Feature Name

Phase D Documentation and Validation

> **Status: partially superseded (2026-07 audit).** The reference scripts shipped under `examples/general/` (`aggregate_sales_by_month.py`, `model_utility_methods.py`, `recordset_functional_ops.py`, `recordset_set_ops.py`, `environment_alterations.py`, `domain_builder.py`). The architecture plan lives at `docs/source/design/odoo-sdk-architecture-plan.md`, not the flat path assumed here. No Phase D symbol needed adding to `src/odoo_sdk/__init__.py` — the whole phase is methods on already-exported types.

# Goal

## Problem

New ORM methods and recordset operations are only discoverable and trustworthy if they are demonstrated against a live Odoo instance, reflected in updated documentation, and confirmed to have no regressions in the Phase A–C test suite.

## Solution

Update all relevant documentation, add examples, run the full test suite, and run live integration smoke tests before marking Phase D complete.

# Requirements

## Functional Requirements

- At least one `examples/` script must demonstrate each of D1–D6 against a live Odoo instance.
- `docs/odoo-sdk-architecture-plan.md` must be updated with Phase D boundary and achievement summary.
- All new public Phase D symbols must be included in `src/odoo_sdk/__init__.py` exports if they are part of the public API.
- The full unit test suite must pass with no regressions.
- Live smoke tests in `examples/` must pass against at least one Odoo instance.

## Non-Functional Requirements

- All examples must be runnable with `uv run python examples/<script>.py` using the local venv.
- Examples must use realistic Odoo model names and fields, not synthetic test fixtures.

# Acceptance Criteria

- [ ] `examples/` contains at least one script per Phase D feature group (aggregation, utility methods, functional ops, set ops, env alterations, domain builder).
- [ ] `docs/odoo-sdk-architecture-plan.md` reflects Phase D completion.
- [ ] `src/odoo_sdk/__init__.py` exports are updated.
- [ ] `uv run coverage run -m unittest -v` passes with no regressions.
- [ ] All Phase D checklist items are marked done.
- [ ] At least one live smoke test run is recorded in the phase notes.

# Out of Scope

- CI pipeline setup.
- Package publishing.
- Performance benchmarking.
