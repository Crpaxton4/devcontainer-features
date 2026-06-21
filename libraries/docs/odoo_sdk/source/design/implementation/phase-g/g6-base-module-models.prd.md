# Feature Name

Base Module Pre-Built Models

# Goal

## Problem

Every Odoo integration uses `res.partner`, `res.users`, `res.company`, and other `base` module models. Without pre-built typed models, each consumer must write their own from scratch and maintain version compatibility independently.

## Solution

Implement typed `OdooBaseModel` subclasses for 12 `base` module models with correct field types and version annotations.

# Requirements

## Functional Requirements

- Implement the following 12 models in `src/odoo_sdk/typing/base/`:
  - `ResPartner` → `res.partner`
  - `ResUsers` → `res.users`
  - `ResCompany` → `res.company`
  - `ResCountry` → `res.country`
  - `ResCountryState` → `res.country.state`
  - `ResCurrency` → `res.currency`
  - `ResLang` → `res.lang`
  - `IrModel` → `ir.model`
  - `IrModelFields` → `ir.model.fields`
  - `IrAttachment` → `ir.attachment`
  - `IrRule` → `ir.rule`
  - `IrConfigParameter` → `ir.config_parameter`
- Each model declares `_supported_versions = ("16.0", "17.0", "18.0", "19.0")`.
- Fields use `OdooField(since=...)` for fields added in Odoo 17.0, 18.0, or 19.0.
- All models use `model_config = ConfigDict(extra='ignore')` to accept extra server-side fields gracefully.
- All 12 models are registered in `TypeRegistry` at import time.

## Non-Functional Requirements

- Field definitions must be verified against Odoo source for 16.0 and 19.0 at minimum.
- Models must be importable with `from odoo_sdk.typing.base import ResPartner`.

# Acceptance Criteria

- [ ] All 12 models instantiate with minimal fields (e.g., `ResPartner(id=1, name='ACME')`).
- [ ] `ResPartner.supports_version('16.0')` returns `True`.
- [ ] `ResPartner.supports_version('15.0')` returns `False`.
- [ ] Fields added in 17.0+ are stripped on `strip_for_version('16.0')`.
- [ ] All 12 models are accessible via `TypeRegistry.resolve`.
- [ ] Unit tests for each model: instantiation, `supports_version`, and `strip_for_version`.

# Out of Scope

- Non-`base` module models.
- Complete field coverage (only the most-used fields per model are included; Phase F dynamic generation covers the rest).
