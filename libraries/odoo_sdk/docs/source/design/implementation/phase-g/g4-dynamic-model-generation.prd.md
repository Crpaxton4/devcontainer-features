# Feature Name

Dynamic Model Generation from Phase F Schema

> **Status: never implemented (2026-07 audit).** No part of Phase G shipped. There is no `src/odoo_sdk/typing/` package and no `OdooBaseModel`, `OdooField`, `TypeRegistry`, or `build_model_from_schema` anywhere in the source. Pydantic is a *required* runtime dependency of the SDK (`pyproject.toml`), not an optional `typing` extra, but it is used for unrelated purposes; no Odoo model is described by a Pydantic class. Phase G also depends on Phase F reflection and Phase E `server_version_string()`, neither of which shipped. Retained as a record of the original Phase G plan.

# Goal

## Problem

When no pre-built or consumer-registered model is available for a model, the caller gets raw dicts even though the Phase F registry has the full schema. The fallback tier should generate a typed model automatically from the live schema.

## Solution

Implement `build_model_from_schema(schema: ModelSchema, server_version: str) -> type[OdooBaseModel]` and integrate it as the third resolution tier in `TypeRegistry.resolve`.

# Requirements

## Functional Requirements

- `build_model_from_schema(schema: ModelSchema, server_version: str) -> type[OdooBaseModel]` — dynamically creates a Pydantic model class using `pydantic.create_model`.
- Field type mapping: `char`, `text`, `html` → `str | None`; `integer` → `int | None`; `float`, `monetary` → `float | None`; `boolean` → `bool = False`; `date` → `datetime.date | None`; `datetime` → `datetime.datetime | None`; `many2one` → `int | None`; `one2many`, `many2many` → `list[int] = []`; `selection` → `str | None`; all other ttypes → `object | None`.
- The generated model has `_odoo_model = schema.name` and `_supported_versions = (server_version,)`.
- `TypeRegistry` caches generated models: subsequent `resolve` for the same model and version returns the cached class.
- If Phase F is unavailable (`env.registry` is not accessible), this tier returns `None` silently.
- If Pydantic is not installed, this function raises `ImportError`.

## Non-Functional Requirements

- Dynamic generation is synchronous.
- Generated class names are derived from the model name: `'res.partner'` → `ResPartner`.

# Acceptance Criteria

- [ ] `build_model_from_schema(schema, '18.0')` returns a class with correct field types.
- [ ] A `many2one` field becomes `int | None`.
- [ ] A `one2many` field becomes `list[int]`.
- [ ] The generated class is cached in `TypeRegistry` after first generation.
- [ ] Phase F unavailable: tier returns `None` without raising.
- [ ] Unit tests cover type mapping for each ttype group and caching behavior.

# Out of Scope

- Generating models for `selection` fields with explicit enum validation (they become `str | None`).
