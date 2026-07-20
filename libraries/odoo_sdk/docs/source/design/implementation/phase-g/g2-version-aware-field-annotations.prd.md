# Feature Name

Version-Aware Field Annotations

> **Status: never implemented (2026-07 audit).** No part of Phase G shipped. There is no `src/odoo_sdk/typing/` package and no `OdooBaseModel`, `OdooField`, `TypeRegistry`, or `build_model_from_schema` anywhere in the source. Pydantic is a *required* runtime dependency of the SDK (`pyproject.toml`), not an optional `typing` extra, but it is used for unrelated purposes; no Odoo model is described by a Pydantic class. Phase G also depends on Phase F reflection and Phase E `server_version_string()`, neither of which shipped. Retained as a record of the original Phase G plan.

# Goal

## Problem

Pre-built models must correctly represent fields that were added or removed between Odoo 16.0 and 19.0. Without version metadata on individual fields, `strip_for_version` cannot know which fields to nullify.

## Solution

Implement `OdooField(default, since, until)` as a Pydantic field factory wrapper that attaches `since` and `until` version metadata, and use it in `strip_for_version`.

# Requirements

## Functional Requirements

- `OdooField(default=None, since: str | None = None, until: str | None = None)` — wraps `pydantic.Field` and attaches version strings as JSON schema extras.
- Version comparison uses `tuple[int, int]` extracted from `'17.0'` → `(17, 0)`.
- `OdooBaseModel.strip_for_version(version)` iterates model fields, checks `OdooField.since`/`until` metadata, and sets out-of-range fields to `None` in the returned copy.
- A field with no `since`/`until` is always included regardless of version.
- A field with `since='17.0'` is included for `17.0`, `18.0`, `19.0`; excluded for `16.0`.
- A field with `until='18.0'` is included for `16.0`, `17.0`, `18.0`; excluded for `19.0`.

## Non-Functional Requirements

- Version metadata is stored using Pydantic v2's `json_schema_extra` mechanism.
- `OdooField` must not break Pydantic's field validation.

# Acceptance Criteria

- [ ] `OdooField(since='17.0')` on a field: present on 17.0+, absent on 16.0.
- [ ] `OdooField(until='18.0')` on a field: absent on 19.0+, present on 18.0 and earlier.
- [ ] Field with no `since`/`until` is always included.
- [ ] Unit tests cover all three cases and edge versions.

# Out of Scope

- Dynamic version detection (that is handled by `server_version()` in Phase E and wired in Phase G5).
