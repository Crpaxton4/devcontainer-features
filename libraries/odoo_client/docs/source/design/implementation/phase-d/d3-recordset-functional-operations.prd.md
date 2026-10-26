# Feature Name

Recordset Functional Operations — `filtered`, `mapped`, `sorted`, `grouped`, `filtered_domain`

# Goal

## Problem

Odoo ORM code routinely uses `filtered`, `mapped`, `sorted`, `grouped`, and `filtered_domain` to process already-fetched recordsets in memory. Without these, SDK consumers write verbose Python list comprehensions over raw dicts, losing the recordset abstraction and the fluent chaining style that makes Odoo ORM code readable.

## Solution

Implement all five operations as methods on `OdooRecordset`. Each operates in-memory against already-fetched field values. None issues additional server calls beyond the initial field fetch required to populate the field cache.

# Requirements

## Functional Requirements

- `filtered(func) -> OdooRecordset` — accepts a callable (predicate returning bool), a dotted field path string (truthy test), or a `DomainExpression` / domain list; returns a new `OdooRecordset` containing only the records that satisfy the predicate.
- `mapped(func) -> list | OdooRecordset` — accepts a callable or a dotted field path string; returns a list for scalar field paths or a new `OdooRecordset` (union) for relational field paths; when `func` is a callable, always returns a list.
- `sorted(key=None, reverse=False) -> OdooRecordset` — accepts a callable, a comma-separated field spec string with optional `ASC`/`DESC` and `NULLS FIRST`/`NULLS LAST` qualifiers, or `None` for the default model order; returns a new `OdooRecordset`.
- `grouped(key) -> dict` — accepts a field name string or callable; returns a `dict` mapping each distinct key value to an `OdooRecordset` of records sharing that value; all returned recordsets share the same prefetch set.
- `filtered_domain(domain) -> OdooRecordset` — accepts a domain list or `DomainExpression`; evaluates the domain against cached field values; returns a new `OdooRecordset`; does not issue a server call.

## Non-Functional Requirements

- All five operations are client-side and synchronous.
- Operations that require field values must fetch them via the existing `read_adapted()` path if not already cached.
- Returned `OdooRecordset` instances must carry the same env as the source recordset.
- `filtered_domain` must raise a clear error if a field referenced in the domain is not fetchable (unknown field).
- Dotted path traversal in `mapped` must follow Many2one chains and return the union recordset for the terminal model.

# Acceptance Criteria

- [ ] `filtered(lambda r: r.active)` returns only records where `active` is truthy.
- [ ] `filtered('partner_id.is_company')` returns only records where the dotted path is truthy.
- [ ] `filtered(domain)` returns only records matching the domain.
- [ ] `mapped('name')` returns a list of name strings.
- [ ] `mapped('partner_id')` returns a deduplicated `OdooRecordset` of related partners.
- [ ] `mapped(lambda r: r.amount_total * 2)` returns a list of computed values.
- [ ] `sorted('name')` returns records in ascending name order.
- [ ] `sorted('name DESC')` returns records in descending name order.
- [ ] `sorted(None)` returns records in the model's default order.
- [ ] `grouped('state')` returns a dict keyed by state value.
- [ ] `filtered_domain([('active', '=', True)])` filters correctly in-memory.
- [ ] Calling any operation on an empty recordset returns an empty result of the correct type.
- [ ] Unit tests cover all five operations with single-record, multi-record, and empty-recordset inputs.

# Out of Scope

- Server-side evaluation of `filtered_domain` (client-side only in Phase D).
- Lazy evaluation of functional operations.
- Support for `filtered` with `@api.onchange`-style side effects.
