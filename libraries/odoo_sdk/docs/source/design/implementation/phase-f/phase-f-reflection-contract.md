# Phase F Reflection Contract

> **Status: never implemented (2026-07 audit).** No part of Phase F shipped. There is no `OdooModelRegistry`, `ModelSchema`, or `FieldSchema` anywhere in `src/odoo_sdk/`, and no `ir.model` / `ir.model.fields` reflection path. The only metadata layer that shipped is the Phase B `MetadataCache` over `fields_get` (`src/odoo_sdk/env/metadata_cache.py`), which this phase was meant to sit beside. `OdooEnv`, which every Phase F surface below hangs off, was itself removed in PR #161. Retained as a record of the original Phase F plan.

## Purpose

This contract is the implementation baseline for Phase F.

Its job is to add a live, cached view of the connected Odoo instance's schema using `ir.model` and `ir.model.fields`, with lazy-by-default discovery, explicit eager loading, TTL-based expiry, and version fingerprinting. Every Phase F task is evaluated against this document before it is accepted.

## Preserved Public Surfaces

| Surface | Phase F status | Guardrail |
|---|---|---|
| `OdooClient` | Preserved | No changes; `env.registry` is the registry entry point |
| `OdooEnv` | Preserved and extended | Gains `registry` property and `get_model_schema` method |
| `OdooRecordset` | Preserved | No changes in Phase F |
| `OdooExecutor` | Preserved | Registry queries use the same executor as all other calls |
| `MetadataCache` | Preserved | Complementary to `OdooModelRegistry`; not replaced |

## Responsibility Boundaries

| Abstraction | Owns in Phase F | Does not own |
|---|---|---|
| `OdooModelRegistry` | `ir.model` + `ir.model.fields` schema cache, lazy fetch, explicit discovery, TTL, version fingerprinting, invalidation | Field adaptation, Pydantic model generation, MCP tools |
| `ModelSchema` | Typed representation of one model's metadata | Validation logic, code generation |
| `FieldSchema` | Typed representation of one field's metadata | Write-side validation, Pydantic fields |
| `OdooEnv` | Registry access point, shared registry across derived envs | Registry implementation |
| `MetadataCache` | `fields_get` response cache for adaptation | `ir.model` level schema |

## Resolved Phase F Decisions

### MetadataCache and OdooModelRegistry Are Complementary

`MetadataCache` (Phase B) caches raw `fields_get` responses and is used by the field adaptation layer. `OdooModelRegistry` (Phase F) fetches from `ir.model` and `ir.model.fields` and produces typed `ModelSchema` / `FieldSchema` objects. Both caches serve different consumers and coexist. Phase F does not replace `MetadataCache`.

### Lazy by Default

Schema discovery happens on first model access. The registry does not fetch any schema at construction time. This keeps connection and client construction instant.

### Always Synchronous

`discover()` blocks until all requested schemas are loaded. There are no background threads. For large instances, callers should pass a specific `models` list to `discover()` rather than loading all models.

### Registry Is Shared Across Derived Envs

When `with_context`, `with_user`, or `with_company` derive a new `OdooEnv`, the derived env shares the same `OdooModelRegistry` instance as its parent. This ensures the schema cache is warmed once and reused, not duplicated per derived env.

### Version Fingerprint Uses Server Version + Module List Hash

The fingerprint is computed from `server_version()` and the list of installed module names fetched from `ir.module.module`. If the fingerprint changes between registry creation and a subsequent access, the entire cache is invalidated and re-fetched lazily.

### No Field Validation in Phase F

Phase F does not add field name validation to `read`, `write`, `search`, or `create`. Field validation against the schema is a Phase G concern where Pydantic models provide the validation layer.

## Explicitly Deferred Work

- Pydantic model generation from `ModelSchema` (Phase G)
- Field validation on write/create using the registry (Phase G)
- MCP schema resources using `ModelSchema` (Phase H)
- Async discovery
- CI or release automation
