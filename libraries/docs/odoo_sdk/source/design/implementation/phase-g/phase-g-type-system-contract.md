# Phase G Type System Contract

## Purpose

This contract is the implementation baseline for Phase G.

Its job is to add an optional Pydantic type layer that provides typed SDK models for the Odoo `base` module, a consumer plugin interface, dynamic model generation from Phase F reflection, and write/create validation. Every Phase G task is evaluated against this document before it is accepted.

## Preserved Public Surfaces

| Surface | Phase G status | Guardrail |
|---|---|---|
| `OdooClient` | Preserved and extended | Gains `type_registry` property |
| `OdooEnv` | Preserved | Used as entry point to `type_registry` via `env.client` |
| `OdooRecordset` | Preserved and extended | Gains `read_typed()`; `write`/`create` gain optional validation |
| `OdooModelRegistry` | Preserved | Used by dynamic generation to fetch `ModelSchema` |
| All Phase A–F exports | Preserved | No regressions permitted |

## Responsibility Boundaries

| Abstraction | Owns in Phase G | Does not own |
|---|---|---|
| `OdooBaseModel` | Base class, `_odoo_model`, `_supported_versions`, `strip_for_version` | Transport, cache, MCP |
| `OdooField` | Version-aware Pydantic field factory, `since`/`until` metadata | Version detection (delegates to `server_version()`) |
| `TypeRegistry` | Three-tier resolution, plugin registration, dynamic model cache | Schema fetching (delegates to `OdooModelRegistry`) |
| `OdooRecordset` | Calling `TypeRegistry.resolve` before write/create, `read_typed()` | Model definition, field validation logic |
| Pre-built `base/` models | Field definitions for 12 base models | Any non-base module model |

## Resolved Phase G Decisions

### Pydantic Is Optional

`pip install odoo_sdk[typing]` installs Pydantic v2. If Pydantic is not installed, all Phase G surfaces must degrade gracefully:
- `TypeRegistry.resolve` returns `None`.
- `write`/`create` proceed without validation.
- `read_typed()` returns raw dicts.
- `OdooBaseModel` import raises `ImportError` with an actionable install message.

### Three-Tier Resolution Order

1. Plugin-registered model (consumer-defined, highest precedence).
2. Pre-built SDK model (if `server_version()` is in `_supported_versions`).
3. Dynamically generated from Phase F `ModelSchema`.
4. Raw dict fallback (when no model is available or Pydantic is absent).

### _supported_versions Is an Explicit Whitelist

Each pre-built model class must declare `_supported_versions: ClassVar[tuple[str, ...]]` as an explicit tuple of version strings. There are no ranges, no wildcards. Adding a new Odoo version to a model requires an explicit code change and test.

### Validation Scope: write and create Only

The validation hook fires in `OdooRecordset.write` and `OdooRecordset.create` before the executor call. Reading (`read`, `read_adapted`, `search_read`) returns whatever the server returns and does not validate. This is intentional: server-returned data may differ from SDK field definitions and must not raise.

### Base Module Scope: 12 Models

Initial pre-built models: `res.partner`, `res.users`, `res.company`, `res.country`, `res.country.state`, `res.currency`, `res.lang`, `ir.model`, `ir.model.fields`, `ir.attachment`, `ir.rule`, `ir.config_parameter`. All other modules are consumer responsibility.

### Dynamic Generation Uses Phase F

When no pre-built or registered model is available, `TypeRegistry.resolve` fetches `ModelSchema` from `OdooModelRegistry` (Phase F) and calls `build_model_from_schema`. If Phase F is unavailable, this tier silently returns `None`.

## Explicitly Deferred Work

- Pre-built models for non-`base` modules.
- Read-time validation (server data may differ from SDK definitions).
- Pydantic v1 support.
- MCP tool schema from typed models (Phase H).
- Async validation.
- CI or release automation.
