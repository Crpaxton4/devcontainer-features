# Feature Name

Validation Integration

# Goal

## Problem

Currently `write` and `create` pass `values` directly to the executor without any local validation. A caller passing a misspelled field name or the wrong value type discovers the error via a server-side exception after a network round-trip.

## Solution

Wire `TypeRegistry.resolve` into `OdooRecordset.write` and `OdooRecordset.create`. When a model is resolved and Pydantic is installed, validate `values` before calling the executor.

# Requirements

## Functional Requirements

- `OdooRecordset.write(values: dict)` — before the executor call, checks `env.client.type_registry.resolve(self.model_name, server_version)`. If a model is resolved and Pydantic is installed, validates `values` as a partial model update (only provided fields are validated, not required fields). Raises `OdooValidationError` with the Pydantic error detail on failure.
- `OdooRecordset.create(values: dict)` — validates `values` as a full model construction. Raises `OdooValidationError` with Pydantic error detail on failure.
- `OdooRecordset.read_typed(fields: list[str] | None = None) -> list[OdooBaseModel]` — calls `read(fields)`, then constructs typed instances using the resolved model. Falls back to raw dicts if no model is resolved.
- When no model is resolved (e.g., Pydantic not installed, model not registered, no Phase F schema): `write`/`create`/`read_typed` proceed exactly as before (no-op validation).
- `server_version` used for resolution is obtained from `env.client.server_version_string()` (Phase E).

## Non-Functional Requirements

- Validation failure must raise before any network call is made.
- The validation path must add zero overhead when Pydantic is not installed or no model is resolved.

# Acceptance Criteria

- [ ] `write({'name': 123})` on `res.partner` raises `OdooValidationError` when `ResPartner` is registered and Pydantic is installed.
- [ ] `write({'name': 'ACME'})` passes validation and reaches the executor.
- [ ] `write({'totally_fake_field': 1})` raises `OdooValidationError` (unknown field).
- [ ] With Pydantic not installed: `write({'totally_fake_field': 1})` passes through without raising.
- [ ] `read_typed()` returns `ResPartner` instances when registered.
- [ ] `read_typed()` returns raw dicts when no model is registered.
- [ ] Unit tests cover all four cases.

# Out of Scope

- Validation on `search`, `search_read`, or `read` (read-side validation is deferred).
- `sudo` path validation (sudo raises `NotImplementedError` in Phase D).
