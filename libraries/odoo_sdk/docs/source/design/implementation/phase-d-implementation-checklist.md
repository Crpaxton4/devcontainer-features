# Phase D Implementation Checklist

> **Status: partially superseded (2026-07 audit).** D1–D4 and D6 shipped on `OdooRecordset` and `DomainExpression`. Not shipped: `with_user` (D5), `OdooEnv` alterations of any kind (the type was removed in PR #161), a `sudo()` `NotImplementedError` stub (D5), and `_read_group`'s `having` argument, which raises `NotImplementedError` (D1). Any item naming `OdooModel` or `OdooQuery` has no subject.

## Objective

Implement the ORM method and recordset operation gaps that exist between the current SDK and idiomatic Odoo ORM usage. Phase D is purely additive to `OdooRecordset`, `OdooEnv`, and `DomainExpression` — no new architecture is introduced.

## PRD-Ready Context

### Problem statement

Phase B and C give the SDK metadata caching, field adaptation, error mapping, and extensibility hooks. But a developer coming from Odoo internal code still finds many ORM methods missing. Aggregation, duplicating records, fetching defaults, verifying record existence with audit metadata, functional operations like `filtered`, `mapped`, `sorted`, and all set-algebra operations on recordsets are absent. Domain composition is also underpowered: there is no `Domain.AND` / `Domain.OR` class-level helper and no support for dynamic time values in domain conditions.

### Desired outcome

- All commonly used Odoo ORM CRUD and utility methods have an SDK equivalent on `OdooRecordset`.
- In-memory functional operations (`filtered`, `mapped`, `sorted`, `grouped`, `filtered_domain`) are available as recordset methods.
- Recordset set operations (`|`, `&`, `-`, `in`, `not in`, subset/superset comparisons) behave consistently.
- Environment alterations (`with_user`, `with_company`) work on both `OdooEnv` and `OdooRecordset`.
- Domain composition helpers (`Domain.AND`, `Domain.OR`, `~`) and dynamic time values are available.
- Active/archived record handling is explicit and consistent.
- `sudo()` is explicitly excluded and documented as a no-op limitation of the external API.

### Non-goals

- No new transport layer.
- No runtime model reflection or schema discovery.
- No Pydantic validation.
- No MCP integration.
- No async facade.
- No CI or release automation.
- No `sudo()` support.

### Constraints

- Preserve established public surfaces: `OdooClient`, `OdooModel`, `OdooQuery`, `OdooExecutor`, and `CommandDispatcher` must remain usable.
- Build on Phase A, B, and C abstractions rather than reopening the core architecture.
- All operations are synchronous.
- No new external dependencies are introduced.

### Success signal

- A developer can write external Odoo integrations using the same method names and semantics they use inside Odoo ORM code.
- All Phase D methods have unit tests and at least one smoke test in `examples/`.
- Missing method gaps are documented explicitly in `phase-d-orm-completeness-contract.md`.

## Execution Order

1. Lock down Phase D boundaries and ORM completeness contract.
2. Add aggregation and groupby support (`_read_group`).
3. Add model utility methods (`name_create`, `name_search`, `default_get`, `copy`, `get_metadata`).
4. Add recordset functional operations (`filtered`, `mapped`, `sorted`, `grouped`, `filtered_domain`).
5. Add recordset set operations and membership tests.
6. Add environment alterations (`with_user`, `with_company`) and active/archived handling.
7. Add domain builder ergonomics (`Domain.AND`, `Domain.OR`, `~`, dynamic time values).
8. Update docs, examples, and local validation.

## Implementation Checklist

## D0 - Phase Guardrails

Goal
- Define the exact Phase D contract before implementation adds new ORM methods and recordset operations.

Likely touch points
- `docs/implementation/phase-d/phase-d-orm-completeness-contract.md`
- `docs/implementation/phase-d-implementation-checklist.md`
- `docs/odoo-sdk-architecture-plan.md`
- `docs/odoo-sdk-design-patterns.md`
- `docs/implementation/phase-c/phase-c-extensibility-contract.md`

Checklist
- [x] Create and adopt a dedicated Phase D ORM completeness contract as the review baseline for D1–D7.
- [x] Confirm the exact Phase D scope: ORM methods, recordset functional ops, set ops, env alterations, domain ergonomics.
- [x] Confirm that `sudo()` is explicitly out of scope and document why (no-op over external API).
- [x] Confirm Phase A, B, and C prerequisites: recordset-first internals, metadata caching, field adaptation, error mapping, and plugin hooks must exist.
- [x] Confirm that all Phase D behavior is synchronous.
- [x] Confirm that no new external dependencies are introduced.

Done when
- The implementation team can evaluate D1 through D7 against the standalone Phase D contract without reopening architecture, transport, or reflection decisions.

## D1 - Aggregation and GroupBy

Goal
- Add `_read_group` to `OdooRecordset` to support aggregation queries with groupby, aggregates, and optional HAVING filtering.

Why this exists
- Reporting and analytics integrations need to aggregate data server-side. Without `_read_group`, every aggregate must be computed client-side from a full record read, which is impractical at scale.

Likely touch points
- `src/odoo_sdk/records/recordset.py`
- `src/odoo_sdk/env/env.py`
- Tests in `tests/test_records/`
- `examples/`

Checklist
- [x] Implement `_read_group(domain, groupby, aggregates, having, offset, limit, order)` on `OdooRecordset`.
- [x] Map the Odoo `_read_group` granularity strings (`day`, `week`, `month`, `quarter`, `year`) through cleanly.
- [x] Support aggregate specifier strings (`field:sum`, `field:count`, `field:avg`, `field:min`, `field:max`, `field:count_distinct`).
- [x] Return a list of tuples matching the Odoo `_read_group` response shape.
- [x] Add unit tests for groupby-only, aggregate-only, and combined groupby+aggregate cases.
- [x] Add unit tests for `having` filtering.
- [x] Add an example demonstrating aggregate reporting in `examples/`.

Done when
- A developer can perform server-side aggregation using the same interface as Odoo's `_read_group` without manually constructing `execute_kw` calls.

PRD inputs captured by this item
- User-visible behavior change: aggregation and groupby become first-class SDK operations.
- Main technical risk: granularity string handling and result type shape may differ across Odoo versions.

## D2 - Model Utility Methods

Goal
- Add `name_create`, `name_search`, `default_get`, `copy`, and `get_metadata` to `OdooRecordset`.

Why this exists
- These are standard Odoo ORM methods that appear in normal integration workflows. Without them, developers bypass the SDK and call `execute_kw` directly.

Likely touch points
- `src/odoo_sdk/records/recordset.py`
- `src/odoo_sdk/fields/values.py` (for adapted return values)
- Tests in `tests/test_records/`

Checklist
- [x] Implement `name_create(name)` — create a record by display name, return a singleton recordset.
- [x] Implement `name_search(name, domain, operator, limit)` — return `[(id, display_name)]` list.
- [x] Implement `default_get(fields)` — return dict of server-side default values for given field names.
- [x] Implement `copy(default=None)` — duplicate the singleton record, return new singleton recordset.
- [x] Implement `get_metadata()` — return list of audit dicts (`id`, `create_uid`, `create_date`, `write_uid`, `write_date`, `xmlid`, `noupdate`).
- [x] Ensure `name_create` and `copy` return `OdooRecordset` instances, not raw ids.
- [x] Add unit tests for each method.
- [x] Document `default_get` behavior: returns only fields explicitly requested.

Done when
- All five utility methods are available on `OdooRecordset` with the same signatures as the Odoo ORM.

PRD inputs captured by this item
- User-visible behavior change: common ORM utility methods no longer require manual `execute_kw` calls.
- Main technical risk: `copy` must delegate correctly to the active executor without leaking a second recordset construction path.

## D3 - Recordset Functional Operations

Goal
- Add `filtered`, `mapped`, `sorted`, `grouped`, and `filtered_domain` as in-memory operations on `OdooRecordset`.

Why this exists
- Odoo ORM code relies heavily on these operations to process records after fetching. Without them, SDK consumers write verbose list comprehensions over raw dicts instead of chaining recordset operations.

Likely touch points
- `src/odoo_sdk/records/recordset.py`
- `src/odoo_sdk/env/env.py` (field cache for lazy access during mapped/filtered)
- Tests in `tests/test_records/`

Checklist
- [x] Implement `filtered(func)` — accepts a callable, dotted field path string, or `DomainExpression`; returns a new `OdooRecordset`.
- [x] Implement `mapped(func)` — accepts a callable or dotted field path; returns a list for scalar fields or a new `OdooRecordset` for relational fields.
- [x] Implement `sorted(key=None, reverse=False)` — accepts a callable, comma-separated field spec string, or `None` for default order; returns a new `OdooRecordset`.
- [x] Implement `grouped(key)` — accepts a field name or callable; returns a `dict` mapping key values to `OdooRecordset` instances.
- [x] Implement `filtered_domain(domain)` — accepts a domain list or `DomainExpression`; evaluates against already-fetched field values; returns a new `OdooRecordset`.
- [x] Ensure all operations preserve the env binding on returned recordsets.
- [x] Add unit tests for each operation, including empty-recordset edge cases.
- [x] Add tests for dotted-path traversal in `mapped` and `filtered`.

Done when
- All five functional operations are available and compose correctly with other recordset operations.

PRD inputs captured by this item
- User-visible behavior change: in-memory record processing matches idiomatic Odoo ORM style.
- Main technical risk: `filtered_domain` evaluation client-side requires fetched field values; must not silently skip unfetched fields.

## D4 - Recordset Set Operations

Goal
- Add set-algebra operators and membership tests to `OdooRecordset` so recordsets behave like ordered sets.

Why this exists
- Odoo ORM code uses `|`, `&`, `-`, `in`, `not in`, and subset/superset comparisons between recordsets constantly. Without these, the SDK cannot support idiomatic code that combines or compares result sets.

Likely touch points
- `src/odoo_sdk/records/recordset.py`
- Tests in `tests/test_records/`

Checklist
- [x] Implement `|` (union) — returns a new recordset with all ids from both operands, preserving order, deduplicating.
- [x] Implement `&` (intersection) — returns a new recordset with ids present in both operands.
- [x] Implement `-` (difference) — returns a new recordset with ids in the left operand that are absent from the right.
- [x] Implement `in` / `not in` — membership test for a singleton recordset in a larger recordset.
- [x] Implement `<=` / `<` — subset and strict subset tests.
- [x] Implement `>=` / `>` — superset and strict superset tests.
- [x] Raise a clear error when set operations are attempted between recordsets of different models.
- [x] Add unit tests for each operator, including empty operands and same-model/different-model combinations.

Done when
- All set operations behave consistently with Odoo ORM recordset set semantics.

PRD inputs captured by this item
- User-visible behavior change: set combination and membership tests work without converting to id lists.
- Main technical risk: preserving order semantics (union should be ordered, not hash-set-sorted) while still deduplicating.

## D5 - Environment Alterations and Active/Archived Handling

Goal
- Add `with_user`, `with_company`, and `active_test` context handling so SDK consumers can change the execution context without reconstructing a client.

Why this exists
- Multi-company workflows, impersonation, and filtering by `active` field are normal in Odoo integrations. Without `with_user` and `with_company`, every context change requires rebuilding `OdooClient`.

Likely touch points
- `src/odoo_sdk/env/env.py`
- `src/odoo_sdk/records/recordset.py`
- Tests in `tests/test_env/` and `tests/test_records/`

Checklist
- [x] Implement `with_user(uid)` on `OdooEnv` and `OdooRecordset` — derives a new env/recordset where subsequent `execute_kw` calls use the given uid.
- [x] Implement `with_company(company_id)` on `OdooEnv` and `OdooRecordset` — derives a new env/recordset with `allowed_company_ids` set to `[company_id]` in context.
- [x] Document explicitly that `sudo()` is NOT implemented — it has no reliable semantic over the external API.
- [x] Implement `action_archive()` on `OdooRecordset` — sets `active=False` on all records in the set.
- [x] Implement `action_unarchive()` on `OdooRecordset` — sets `active=True` on all records in the set.
- [x] Implement `active_test` context key handling — when `active_test=False` is in context, pass it through to search calls so archived records are included.
- [x] Add unit tests for `with_user`, `with_company`, `action_archive`, and `action_unarchive`.
- [x] Add a test asserting that `with_user` derivation leaves the original env unchanged.

Done when
- Context alterations work as derived env/recordset creation without mutating existing state.

PRD inputs captured by this item
- User-visible behavior change: multi-company and user-switch workflows no longer require client reconstruction.
- Main technical risk: `with_user` changes the `uid` field on the executor call; must not mutate a shared executor.

## D6 - Domain Builder Ergonomics

Goal
- Add `Domain.AND`, `Domain.OR`, `~`-based negation, and dynamic time value support to `DomainExpression`.

Why this exists
- The current `DomainExpression` can normalize and serialize but lacks class-level composition helpers. Developers building complex queries must compose raw prefix-notation lists manually.

Likely touch points
- `src/odoo_sdk/query/domain.py`
- Tests in `tests/test_query/`

Checklist
- [x] Add `DomainExpression.AND(iterable)` class method — combines an iterable of domains with `&` operators.
- [x] Add `DomainExpression.OR(iterable)` class method — combines an iterable of domains with `|` operators.
- [x] Add `DomainExpression.TRUE` and `DomainExpression.FALSE` class-level constants.
- [x] Implement `__invert__` (`~`) operator on `DomainExpression` — wraps the domain in a `!` negation node.
- [x] Implement `__and__` (`&`) and `__or__` (`|`) operators on `DomainExpression` for pairwise composition.
- [x] Add support for dynamic time value strings in condition values (`'now'`, `'-3d'`, `'=monday -1w'`, etc.) — pass through to the server unchanged, document the format.
- [x] Add unit tests for each composition method, including empty input and single-element input.
- [x] Add unit tests for `~`, `&`, and `|` operators with nested domains.

Done when
- Domain composition is ergonomic and matches the `Domain.AND([d1, d2])` / `~d1` style described in the Odoo ORM documentation.

PRD inputs captured by this item
- User-visible behavior change: complex domain composition no longer requires raw list manipulation.
- Main technical risk: `AND([])` and `OR([])` edge cases need defined semantics (TRUE and FALSE domains respectively).

## D7 - Documentation and Validation

Goal
- Update all phase documentation, examples, and local validation scripts to reflect the Phase D additions.

Why this exists
- Without updated documentation and examples, new methods are invisible to consumers and untested against a real Odoo instance.

Likely touch points
- `docs/odoo-sdk-architecture-plan.md`
- `examples/`
- `docs/implementation/phase-d/`
- Local integration check scripts

Checklist
- [x] Add or update `examples/` scripts demonstrating each D1–D6 capability against a live Odoo instance.
- [x] Update `docs/odoo-sdk-architecture-plan.md` with Phase D boundary and achievement summary.
- [x] Update public `__init__.py` exports for any new Phase D public symbols.
- [x] Run full test suite and confirm no Phase A–C regressions.
- [x] Run live integration smoke tests in `examples/` against at least one Odoo instance.
- [x] Mark all Phase D checklist items done.

Done when
- Phase D changes are validated locally with both unit tests and live integration checks, and the documentation reflects the new SDK capabilities.

## Detailed PRDs

```{toctree}
:maxdepth: 1
:glob:

phase-d/*
```
