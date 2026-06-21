# Feature Name

Phase E Documentation and Validation

# Goal

## Problem

Adding a second transport is only trustworthy if it is demonstrated to produce identical results to XML-RPC against a real Odoo instance and the documentation clearly explains the migration path.

## Solution

Update docs, add examples, run the full test suite, and run a cross-transport comparison smoke test.

# Requirements

## Functional Requirements

- An `examples/` script must demonstrate `OdooClient.from_json2` doing a complete search/read/write cycle.
- An `examples/` script must demonstrate API key generation and revocation.
- `docs/odoo-sdk-architecture-plan.md` must be updated with Phase E boundary and achievement summary.
- `src/odoo_sdk/__init__.py` must export `OdooJson2Executor`.
- All Phase A–D unit tests must pass unchanged.
- A cross-transport smoke test must run the same query over XML-RPC and JSON-2 against the same live instance and assert identical results.

## Non-Functional Requirements

- Examples must be runnable with `uv run python examples/<script>.py`.

# Acceptance Criteria

- [ ] `examples/` contains JSON-2 and API key examples.
- [ ] Cross-transport smoke test passes.
- [ ] `docs/odoo-sdk-architecture-plan.md` updated.
- [ ] `src/odoo_sdk/__init__.py` exports `OdooJson2Executor`.
- [ ] Full test suite passes with no regressions.
- [ ] All Phase E checklist items are marked done.

# Out of Scope

- CI setup.
- Package publishing.
