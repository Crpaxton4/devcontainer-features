# Feature Name

Model Utility Methods — `name_create`, `name_search`, `default_get`, `copy`, `get_metadata`

# Goal

## Problem

Five standard Odoo ORM methods appear regularly in integration workflows but are missing from `OdooRecordset`. Developers call `execute_kw` directly for these operations, bypassing the SDK's adaptation, error mapping, and context handling. This produces inconsistent code and defeats the purpose of the recordset-first architecture.

## Solution

Implement all five methods on `OdooRecordset` with the same signatures and return semantics as the Odoo ORM. Return types that are records become `OdooRecordset` instances; return types that are data remain adapted dicts or typed values.

# Requirements

## Functional Requirements

- `name_create(name: str) -> OdooRecordset` — calls `name_create` on the server; returns the new record as a singleton `OdooRecordset` for the same model.
- `name_search(name: str = '', domain=None, operator: str = 'ilike', limit: int = 100) -> list[tuple[int, str]]` — returns a list of `(id, display_name)` pairs.
- `default_get(fields: list[str]) -> dict` — returns a dict of server-side default values keyed by field name; only fields present in the response are included.
- `copy(default: dict | None = None) -> OdooRecordset` — duplicates the singleton record; returns the new record as a singleton `OdooRecordset`; raises `OdooError` if called on a recordset with more than one record.
- `get_metadata() -> list[dict]` — returns a list of audit dicts with keys: `id`, `create_uid`, `create_date`, `write_uid`, `write_date`, `xmlid`, `xmlids`, `noupdate`; one dict per record in the recordset.

## Non-Functional Requirements

- `name_create` and `copy` must return `OdooRecordset` instances, not raw ids.
- `copy` must call `ensure_one()` before delegating to the server.
- All five methods must pass the current env context to the server call.
- All five methods must be synchronous.

# Acceptance Criteria

- [ ] `name_create('Test Name')` returns a singleton `OdooRecordset` for the model.
- [ ] `name_search('test')` returns a list of `(int, str)` pairs.
- [ ] `default_get(['name', 'active'])` returns a dict containing only the fields for which the server has defaults.
- [ ] `copy()` returns a new singleton `OdooRecordset` distinct from the original.
- [ ] `copy()` raises an error when the recordset contains more than one record.
- [ ] `get_metadata()` returns one dict per record in the recordset.
- [ ] Unit tests exist for each method, including empty-recordset edge cases for `get_metadata`.

# Out of Scope

- Client-side tracking of metadata changes.
- Caching of `default_get` results.
- Typed return values for `get_metadata` fields (raw dict is sufficient in Phase D).
