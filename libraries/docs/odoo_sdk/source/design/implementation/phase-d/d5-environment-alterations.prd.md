# Feature Name

Environment Alterations and Active/Archived Handling

# Goal

## Problem

Multi-company workflows require switching `allowed_company_ids` in context. User-switch workflows require changing the `uid` on calls without rebuilding the entire client. Archived record handling requires an `active_test` context flag to include inactive records in searches. None of these are available today without reconstructing `OdooClient`.

## Solution

Add `with_user`, `with_company`, and `action_archive` / `action_unarchive` to `OdooEnv` and `OdooRecordset`. All context alterations follow the established derivation pattern: they return new instances and never mutate existing state. `sudo()` is explicitly excluded with documented rationale.

# Requirements

## Functional Requirements

- `OdooEnv.with_user(uid: int) -> OdooEnv` — returns a new `OdooEnv` where subsequent `execute_kw` calls use the given uid; the original env is unchanged.
- `OdooRecordset.with_user(uid: int) -> OdooRecordset` — delegates to `env.with_user(uid)` and returns a new recordset bound to the derived env.
- `OdooEnv.with_company(company_id: int) -> OdooEnv` — returns a new `OdooEnv` with `allowed_company_ids=[company_id]` merged into context.
- `OdooRecordset.with_company(company_id: int) -> OdooRecordset` — delegates to `env.with_company(company_id)` and returns a new recordset.
- `OdooRecordset.action_archive() -> bool` — calls `write({'active': False})` on all records in the recordset.
- `OdooRecordset.action_unarchive() -> bool` — calls `write({'active': True})` on all records in the recordset.
- `active_test` context key — when present in env context with value `False`, it is passed through to server calls, causing Odoo to include archived records in search results.
- `sudo()` must not be implemented; if a consumer calls `.sudo()`, the method should raise `NotImplementedError` with a clear message explaining why it is excluded.

## Non-Functional Requirements

- `with_user` and `with_company` must not mutate the shared executor; uid override must be applied per-call.
- All derivations are synchronous and immediate.
- Derived envs carry the same metadata cache reference as the parent to avoid redundant cache warming.

# Acceptance Criteria

- [ ] `env.with_user(7)` returns a new env; the original env still uses the old uid.
- [ ] `env.with_company(3)` returns a new env with `allowed_company_ids=[3]` in context; the original context is unchanged.
- [ ] `recordset.with_user(7)` returns a new recordset bound to the new env.
- [ ] `recordset.with_company(3)` returns a new recordset bound to the new env.
- [ ] `recordset.action_archive()` writes `active=False` and returns `True`.
- [ ] `recordset.action_unarchive()` writes `active=True` and returns `True`.
- [ ] Calling `.sudo()` raises `NotImplementedError` with a message referencing the external API limitation.
- [ ] A search performed with `env.with_context(active_test=False)` passes the flag to the server.
- [ ] Unit tests for `with_user`, `with_company`, derivation immutability, `action_archive`, and `action_unarchive`.

# Out of Scope

- `sudo()` support (permanently excluded).
- Multi-company context with more than one company id in `allowed_company_ids` (single-company switch only in Phase D).
- Server-side permission checks for the switched user.
