# Feature Name

OdooRecordset Core Identity Abstraction

# Goal

## Problem

The current SDK can return ids and raw record dictionaries, but it has no stable abstraction for model identity plus a bound set of ids plus execution context. That gap makes it hard to mirror Odoo ORM semantics, route relation behavior later, or give future features a consistent place to live. Without a recordset, the SDK would continue to spread identity and context concerns across `OdooModel`, `OdooQuery`, and ad hoc payload handling.

## Solution

Introduce `OdooRecordset` as the Phase A core abstraction. A recordset will carry model name, ordered ids, and an `OdooEnv`, and it will provide the minimum record-oriented operations needed for the MVP architecture: `read`, `write`, `unlink`, `exists`, `browse`, `search`, and `with_context`. This establishes a stable core while preserving explicit extraction methods for callers that still need raw rows.

# Requirements

## Functional Requirements

- A new `OdooRecordset` abstraction must exist in the `odoo_service` package.
- `OdooRecordset` must store at minimum the bound model name, the ordered record ids, and the env that owns execution context.
- The recordset must expose explicit identity access so downstream code can inspect its model and ids without reaching into transport-layer details.
- `read(fields=None)` must remain the explicit extraction method for materializing raw record dictionaries.
- `write(values)` must apply updates to the current ids using the recordset's env-bound execution context.
- `unlink()` must remove the current ids using the recordset's env-bound execution context.
- `exists()` must return recordset-oriented existence behavior that preserves stable identity semantics for remaining ids.
- `browse(ids)` must return a recordset for the same model and derived ids.
- `search(domain, limit=None, offset=None, order=None)` must return a recordset for matching ids rather than a query builder.
- `with_context(context)` must return a new recordset bound to a derived environment rather than mutating the current recordset.
- The design must explicitly define which Phase A operations return raw rows and which return recordsets.
- The recordset return contract must not by itself force immediate caller-facing return changes on preserved `OdooModel` or `OdooQuery` compatibility surfaces during Phase A.
- The implementation must define how empty-id behavior is handled for recordset operations and keep compatibility wrappers aligned with that decision.
- Local unit tests must cover recordset creation, identity inspection, chaining behavior, `with_context`, `search`, `browse`, `exists`, and extraction behavior.

## Non-Functional Requirements

- Recordset identity must be immutable from the caller's perspective.
- Construction and chaining must not trigger hidden network I/O unless an operation explicitly requires execution.
- The abstraction must remain narrow enough to avoid becoming a second query builder.
- Context and id handling must preserve input order and avoid accidental mutation leakage.
- Compatibility wrappers may preserve existing caller-facing return shapes during Phase A even while the recordset becomes the internal identity-bearing core.

# Acceptance Criteria

- [ ] A recordset instance can be created with a model name, ids, and env and can report that identity consistently.
- [ ] `with_context` returns a new recordset whose env reflects merged context while the original recordset remains unchanged.
- [ ] `browse` and `search` return recordsets rather than raw rows.
- [ ] `read` returns raw record dictionaries as an explicit extraction step.
- [ ] `exists` returns recordset-oriented results that preserve the original order of surviving ids.
- [ ] Unit tests cover recordset identity, chaining, and context behavior.

# Out of Scope

- Metadata-driven field adaptation.
- Automatic relational traversal or attribute-based lazy loading.
- x2many command helpers.
- Async recordset behavior or background execution.
