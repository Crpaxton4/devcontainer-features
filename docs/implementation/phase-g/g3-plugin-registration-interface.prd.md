# Feature Name

TypeRegistry Plugin Interface

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
