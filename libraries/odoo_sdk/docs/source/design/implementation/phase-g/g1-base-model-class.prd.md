# Feature Name

OdooBaseModel Base Class

> **Status: never implemented (2026-07 audit).** No part of Phase G shipped. There is no `src/odoo_sdk/typing/` package and no `OdooBaseModel`, `OdooField`, `TypeRegistry`, or `build_model_from_schema` anywhere in the source. Pydantic is a *required* runtime dependency of the SDK (`pyproject.toml`), not an optional `typing` extra, but it is used for unrelated purposes; no Odoo model is described by a Pydantic class. Phase G also depends on Phase F reflection and Phase E `server_version_string()`, neither of which shipped. Retained as a record of the original Phase G plan.

# Goal

## Problem

Without a shared base class, every pre-built and consumer-defined typed model must independently implement `_odoo_model`, `_supported_versions`, version compatibility checks, and field stripping. This leads to duplication and inconsistency.

## Solution

Define `OdooBaseModel` as a Pydantic BaseModel subclass with `_odoo_model` and `_supported_versions` class attributes and version utility methods.

# Requirements

## Functional Requirements

- `OdooBaseModel(pydantic.BaseModel)` with `_odoo_model: ClassVar[str]` and `_supported_versions: ClassVar[tuple[str, ...]]`.
- `OdooBaseModel.supports_version(version: str) -> bool` — class method; returns `True` if `version` is in `_supported_versions`.
- `OdooBaseModel.strip_for_version(version: str) -> OdooBaseModel` — returns a copy of the instance where fields whose `OdooField.since` is later than `version` are set to `None` (or their default).
- Importing `OdooBaseModel` when Pydantic is not installed raises `ImportError` with message: `"OdooBaseModel requires pydantic. Install it with: pip install odoo_sdk[typing]"`.
- `pyproject.toml` gains `[project.optional-dependencies] typing = ["pydantic>=2.0"]`.
- `OdooBaseModel` must not declare any Odoo-specific fields itself; it is a pure base class.

## Non-Functional Requirements

- Pydantic v2 only (v1 is not supported).
- The class must be compatible with Pydantic's `model_config = ConfigDict(extra='ignore')` to allow server data with extra fields.

# Acceptance Criteria

- [ ] `OdooBaseModel.supports_version('18.0')` works correctly.
- [ ] `OdooBaseModel.strip_for_version('16.0')` nullifies fields annotated `since='17.0'`.
- [ ] Importing without Pydantic raises `ImportError` with the expected message.
- [ ] `pyproject.toml` has the `typing` optional extra.
- [ ] Unit tests cover `supports_version`, `strip_for_version`, and the absent-Pydantic error.

# Out of Scope

- Field definitions (G6).
- TypeRegistry wiring (G3).
