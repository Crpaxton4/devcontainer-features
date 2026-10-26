# Feature Name

Recordset Set Operations

# Goal

## Problem

Odoo ORM code treats recordsets as ordered sets and uses union, intersection, difference, membership, and subset/superset tests routinely. The current `OdooRecordset` has no set operators. Developers who need to combine or compare result sets must extract id lists, perform Python set operations, and reconstruct recordsets manually — losing context, order, and the SDK's adaptation layer.

## Solution

Implement all standard set algebra operators and membership tests directly on `OdooRecordset`. Results are new `OdooRecordset` instances bound to the same env. Cross-model operations raise an explicit error.

# Requirements

## Functional Requirements

- `a | b` (union) — returns a new `OdooRecordset` containing all records from both operands; deduplicates by id; preserves the order of `a` then adds any ids from `b` not already present.
- `a & b` (intersection) — returns a new `OdooRecordset` containing only records whose ids appear in both operands; preserves the order of `a`.
- `a - b` (difference) — returns a new `OdooRecordset` containing only records in `a` whose ids are absent from `b`; preserves the order of `a`.
- `record in recordset` — membership test; `record` must be a singleton `OdooRecordset`; returns `True` if the record's id is present in the recordset.
- `record not in recordset` — inverse of `in`.
- `a <= b` (subset) — returns `True` if every id in `a` is present in `b`.
- `a < b` (strict subset) — returns `True` if `a <= b` and `a != b`.
- `a >= b` (superset) — returns `True` if every id in `b` is present in `a`.
- `a > b` (strict superset) — returns `True` if `a >= b` and `a != b`.
- All set operations must raise `ValueError` when operands are from different models.

## Non-Functional Requirements

- Set operations must not issue server calls.
- Returned recordsets carry the env of the left operand.
- Operations must be synchronous.
- Performance is O(n) in the number of ids using a set for membership lookups.

# Acceptance Criteria

- [ ] `a | b` returns a recordset with ids from both, deduplicated, `a`-order first.
- [ ] `a & b` returns only shared ids.
- [ ] `a - b` returns only ids in `a` not in `b`.
- [ ] `r in rs` returns `True` when `r`'s id is present in `rs`.
- [ ] `r not in rs` returns `True` when `r`'s id is absent from `rs`.
- [ ] `a <= b`, `a < b`, `a >= b`, `a > b` return correct boolean values.
- [ ] Cross-model union raises `ValueError`.
- [ ] Operating on two empty recordsets returns an empty recordset.
- [ ] Operating on an empty and a non-empty recordset produces correct results.
- [ ] Unit tests cover all operators with same-model and cross-model combinations.

# Out of Scope

- Multiset (duplicate-preserving) operations.
- Ordered-set comparison using element order rather than id membership.
