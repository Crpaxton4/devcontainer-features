# Feature Name

TypeRegistry Plugin Interface

> **Status: never implemented (2026-07 audit).** No part of Phase G shipped. There is no `src/odoo_sdk/typing/` package and no `OdooBaseModel`, `OdooField`, `TypeRegistry`, or `build_model_from_schema` anywhere in the source. Pydantic is a *required* runtime dependency of the SDK (`pyproject.toml`), not an optional `typing` extra, but it is used for unrelated purposes; no Odoo model is described by a Pydantic class. Phase G also depends on Phase F reflection and Phase E `server_version_string()`, neither of which shipped. Retained as a record of the original Phase G plan.

# Goal

## Problem

Consumers working with custom Odoo models (outside `base`) need the same three-tier type resolution without forking the SDK or re-implementing the resolution pipeline.

## Solution

Implement `TypeRegistry` with `register` / `resolve` and wire it to `OdooClient.type_registry`.

# Requirements

## Functional Requirements

- `TypeRegistry` class with `register(model_name: str, model_class: type[OdooBaseModel]) -> None`.
- `TypeRegistry.get(model_name: str) -> type[OdooBaseModel] | None` — returns the directly registered model or `None`.
- `TypeRegistry.resolve(model_name: str, server_version: str) -> type[OdooBaseModel] | None` — three-tier resolution:
  1. Return the plugin-registered model if present.
  2. Return the pre-built SDK model if it supports `server_version`.
  3. Attempt dynamic generation from Phase F `ModelSchema`.
  4. Return `None` if all tiers fail.
- `OdooClient.type_registry -> TypeRegistry` — lazy property; initializes one `TypeRegistry` per client instance.
- The registry dict is `dict[str, type[OdooBaseModel]]` protected by `threading.RLock`.
- Pre-built `base` models are registered at import time in `TypeRegistry.__init__` or via module-level auto-registration.

## Non-Functional Requirements

- Thread-safe for concurrent register and resolve calls.
- `resolve` must not raise; it returns `None` on any failure.

# Acceptance Criteria

- [ ] `client.type_registry.register('account.move', MyMove)` is reflected in `resolve('account.move', '18.0')`.
- [ ] `resolve('res.partner', '18.0')` returns the pre-built `ResPartner` model.
- [ ] `resolve('unknown.model', '18.0')` returns `None` (or a dynamically generated model if Phase F schema is available).
- [ ] Thread-safety test: 10 concurrent `register` calls do not corrupt the dict.
- [ ] Unit tests cover all three resolution tiers.

# Out of Scope

- Model unregistration.
- Namespace / module-level grouping of registrations.
