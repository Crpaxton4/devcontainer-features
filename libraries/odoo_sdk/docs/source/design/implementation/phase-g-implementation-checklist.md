# Phase G Implementation Checklist

> **Status: never implemented (2026-07 audit).** No part of Phase G shipped. There is no `src/odoo_sdk/typing/` package and no `OdooBaseModel`, `OdooField`, `TypeRegistry`, or `build_model_from_schema` anywhere in the source. Pydantic is a *required* runtime dependency of the SDK (`pyproject.toml`), not an optional `typing` extra, but it is used for unrelated purposes; no Odoo model is described by a Pydantic class. Phase G also depends on Phase F reflection and Phase E `server_version_string()`, neither of which shipped. Retained as a record of the original Phase G plan.

## Objective

Add an optional Pydantic type layer (`pip install odoo_sdk[typing]`) that provides pre-built typed models for the Odoo `base` module, a plugin registration interface for consumer-defined models, dynamic model generation from Phase F reflection, and validation integration on `write`/`create`. When Pydantic is not installed, every Phase G surface degrades gracefully to a no-op or returns raw dicts.

## PRD-Ready Context

### Problem statement

The SDK returns raw dicts from `read` and `read_adapted`. Consumers must manually write type stubs or dataclasses to work with field data. When models change between Odoo versions, consumers discover the change at runtime via key errors. There is no shared vocabulary of field names and types for the `base` module models that every integration needs.

### Desired outcome

- An `OdooBaseModel` class (Pydantic BaseModel subclass) defines the interface for SDK-typed models.
- Pre-built models for 12 `base` module models (`res.partner`, `res.users`, `res.company`, `res.country`, `res.country.state`, `res.currency`, `res.lang`, `ir.model`, `ir.model.fields`, `ir.attachment`, `ir.rule`, `ir.config_parameter`) with version-aware fields.
- `OdooField(default, since, until)` — a Pydantic field factory that annotates version compatibility.
- A `TypeRegistry` plugin interface (`client.type_registry.register(model_name, model_class)`) mirrors the `CommandDispatcher` pattern.
- Three-tier type resolution: plugin-registered → pre-built SDK (if version matches) → dynamic from Phase F reflection → raw dict fallback.
- `recordset.read_typed()` returns a list of typed model instances.
- `create` and `write` validate input against the registered model when one exists.
- Each pre-built model declares `_supported_versions: tuple[str, ...]` — an explicit whitelist.
- Fields not available in the connected server version are stripped from instances.

### Non-goals

- No Pydantic model generation for non-`base` modules (those are consumer responsibility).
- No async validation.
- No CI or release automation.
- No strict input coercion beyond what Pydantic does by default.

### Constraints

- Pydantic is an optional dependency: `pip install odoo_sdk[typing]`.
- If Pydantic is not installed, all Phase G surfaces must degrade gracefully.
- All operations are synchronous.
- Version stripping uses `server_version()` from Phase E and schema from Phase F.

### Success signal

- `client.type_registry.register('res.partner', MyResPartner)` works and is reflected in `recordset.read_typed()`.
- Pre-built `ResPartner` model resolves with correct fields for the connected server version.
- `recordset.write({'nonexistent_field': 1})` raises `OdooValidationError` when a typed model is registered.
- Full test suite passes; no Phase A–F regressions.

## Execution Order

1. Lock down Phase G boundaries and type system contract.
2. Implement `OdooBaseModel` base class.
3. Implement `OdooField` version-aware field factory.
4. Implement `TypeRegistry` plugin interface.
5. Implement dynamic model generation from Phase F reflection.
6. Wire validation into `write`/`create`.
7. Implement pre-built `base` module models.
8. Update docs, examples, and validation.

## Implementation Checklist

## G0 - Phase Guardrails

Goal
- Define the exact Phase G contract before any type system work begins.

Likely touch points
- `docs/implementation/phase-g/phase-g-type-system-contract.md`
- `docs/implementation/phase-g-implementation-checklist.md`

Checklist
- [ ] Create and adopt a dedicated Phase G type system contract.
- [ ] Confirm Pydantic is optional and all surfaces degrade to no-op when absent.
- [ ] Confirm the three-tier resolution order.
- [ ] Confirm `_supported_versions` is an explicit whitelist tuple.
- [ ] Confirm dynamic generation uses Phase F `ModelSchema`/`FieldSchema`.
- [ ] Confirm validation scope: write/create only.
- [ ] Confirm initial `base` module model list.

Done when
- G1–G7 PRD authors can validate their tasks against the contract.

## G1 - OdooBaseModel Base Class

Goal
- Define the `OdooBaseModel` base class that all SDK-typed models inherit from.

Why this exists
- A common base class provides the shared `_odoo_model` and `_supported_versions` class attributes and integrates with the type registry and version stripping logic.

Likely touch points
- New `src/odoo_sdk/typing/__init__.py`
- New `src/odoo_sdk/typing/base_model.py`
- `pyproject.toml` (optional Pydantic dependency under `[typing]` extra)
- Tests in `tests/test_typing/`

Checklist
- [ ] `OdooBaseModel(pydantic.BaseModel)` with `_odoo_model: ClassVar[str]` and `_supported_versions: ClassVar[tuple[str, ...]]`.
- [ ] `OdooBaseModel.supports_version(version: str) -> bool` class method.
- [ ] `OdooBaseModel.strip_for_version(version: str) -> OdooBaseModel` — returns a new instance with fields unavailable in `version` set to `None`.
- [ ] If Pydantic is not installed, importing `OdooBaseModel` raises `ImportError` with a clear install message.
- [ ] `pyproject.toml` gains `[project.optional-dependencies] typing = ["pydantic>=2.0"]`.
- [ ] Unit tests cover `supports_version` and `strip_for_version`.

Done when
- `OdooBaseModel` is importable, Pydantic is an optional dependency, and the class attributes are enforced.

PRD inputs captured by this item
- User-visible behavior change: typed SDK models have a consistent base class.
- Main technical risk: Pydantic v1 vs v2 API differences; pin to Pydantic v2.

## G2 - Version-Aware Field Annotations

Goal
- Implement `OdooField(default, since, until)` that annotates when a field was added or removed across Odoo versions.

Why this exists
- Pre-built models must work against Odoo 16.0, 17.0, 18.0, and 19.0. Fields added in 17.0 must be absent (or `None`) when connecting to 16.0. `OdooField` carries this metadata so `strip_for_version` can correctly zero out unavailable fields.

Likely touch points
- `src/odoo_sdk/typing/field.py`
- `src/odoo_sdk/typing/base_model.py` (strip logic)
- Tests in `tests/test_typing/`

Checklist
- [ ] `OdooField(default=None, since: str | None = None, until: str | None = None)` — wraps `pydantic.Field` and attaches `since` and `until` version strings.
- [ ] Version comparison uses tuple comparison on `(major, minor)` (e.g., `'17.0'` → `(17, 0)`).
- [ ] `OdooBaseModel.strip_for_version` uses `OdooField` metadata to determine which fields to nullify.
- [ ] Fields with no `since`/`until` annotation are always included.
- [ ] Unit tests: field with `since='17.0'` is present on 17.0, absent on 16.0.

Done when
- Version-gated fields are correctly stripped when the connected Odoo version does not support them.

PRD inputs captured by this item
- User-visible behavior change: models degrade gracefully on older Odoo versions.
- Main technical risk: `since`/`until` metadata must be accessible via Pydantic's field info mechanism without monkey-patching.

## G3 - TypeRegistry Plugin Interface

Goal
- Implement `TypeRegistry` and wire it to `OdooClient` so consumers can register custom typed models.

Why this exists
- Pre-built models cover only the `base` module. Consumers working with custom Odoo models need the same three-tier resolution without forking the SDK. The plugin interface (mirroring `CommandDispatcher`) provides this.

Likely touch points
- New `src/odoo_sdk/typing/registry.py`
- `src/odoo_sdk/client/client.py` (add `type_registry` property)
- Tests in `tests/test_typing/`

Checklist
- [ ] `TypeRegistry` class with `register(model_name: str, model_class: type[OdooBaseModel])`.
- [ ] `TypeRegistry.get(model_name: str) -> type[OdooBaseModel] | None`.
- [ ] `TypeRegistry.resolve(model_name: str, server_version: str) -> type[OdooBaseModel] | None` — checks: registered plugin → pre-built SDK model (if version matches) → returns `None` for dynamic generation.
- [ ] `OdooClient.type_registry -> TypeRegistry` property (lazy-initialized).
- [ ] Thread-safe registry dict.
- [ ] Unit tests for register, get, resolve (all three tiers), and thread safety.

Done when
- `client.type_registry.register('my.model', MyModel)` wires the model into the resolution pipeline.

PRD inputs captured by this item
- User-visible behavior change: consumers extend the type system without forking the SDK.
- Main technical risk: resolution order must be deterministic; document and test it explicitly.

## G4 - Dynamic Model Generation

Goal
- Implement dynamic `OdooBaseModel` subclass generation from a Phase F `ModelSchema` when no pre-built or registered model is available.

Why this exists
- For models not in the `base` module set and not registered by the consumer, the SDK can still provide a typed interface using the live schema from Phase F reflection. This is the "fallback" tier in the three-tier resolution.

Likely touch points
- New `src/odoo_sdk/typing/dynamic.py`
- `src/odoo_sdk/typing/registry.py` (integrate into `resolve`)
- Tests in `tests/test_typing/`

Checklist
- [ ] `build_model_from_schema(schema: ModelSchema, server_version: str) -> type[OdooBaseModel]` — creates a Pydantic model class with fields derived from `FieldSchema.ttype`.
- [ ] Type mapping: `char`, `text`, `html` → `str | None`; `integer` → `int | None`; `float`, `monetary` → `float | None`; `boolean` → `bool`; `date` → `datetime.date | None`; `datetime` → `datetime.datetime | None`; `many2one` → `int | None`; `one2many`, `many2many` → `list[int]`; `selection` → `str | None`; all others → `object | None`.
- [ ] Generated model has `_odoo_model` and `_supported_versions = (server_version,)`.
- [ ] Generated models are cached in `TypeRegistry` after first generation.
- [ ] If Phase F is not available (no registry), dynamic generation returns `None` and falls back to raw dict.
- [ ] Unit tests: generate from a mock schema and assert field types.

Done when
- `TypeRegistry.resolve('account.move', '18.0')` returns a dynamically generated model backed by Phase F schema.

PRD inputs captured by this item
- User-visible behavior change: any model with a Phase F schema automatically gets a typed interface.
- Main technical risk: `ttype` → Python type mapping must handle all known Odoo field types.

## G5 - Validation Integration

Goal
- Wire model validation into `OdooRecordset.write` and `OdooRecordset.create` when a typed model is registered for the model.

Why this exists
- Without validation, the typed model layer is documentation-only. Failing fast at the SDK boundary with a clear `OdooValidationError` is far preferable to a cryptic server-side error after a round-trip.

Likely touch points
- `src/odoo_sdk/records/recordset.py`
- `src/odoo_sdk/transport/errors.py` (confirm `OdooValidationError` is appropriate)
- Tests in `tests/test_typing/`

Checklist
- [ ] `OdooRecordset.write(values)` and `OdooRecordset.create(values)` check `env.client.type_registry.resolve(self.model_name, server_version)`.
- [ ] If a model is resolved and Pydantic is installed: validate `values` against the model; raise `OdooValidationError` with Pydantic's error detail if validation fails.
- [ ] If no model is resolved or Pydantic is not installed: proceed as before (no-op validation).
- [ ] Validation occurs before the executor call; no round-trip on validation failure.
- [ ] `recordset.read_typed() -> list[OdooBaseModel]` — calls `read()` and returns instances of the resolved model; falls back to raw dicts if no model is resolved.
- [ ] Unit tests: validation failure raises `OdooValidationError`; valid data passes through; no-model path is unchanged.

Done when
- `write({'nonexistent_field': 1})` on a model with a registered type raises `OdooValidationError` before touching the network.

PRD inputs captured by this item
- User-visible behavior change: type errors surface at the SDK boundary.
- Main technical risk: validation must not break the no-Pydantic path; all checks must be gated on `_PYDANTIC_AVAILABLE`.

## G6 - Base Module Pre-Built Models

Goal
- Implement typed `OdooBaseModel` subclasses for the 12 `base` module models.

Why this exists
- The `base` module models are present on every Odoo instance. Pre-built models with correct field types and version annotations give the SDK immediate utility without any consumer configuration.

Likely touch points
- New `src/odoo_sdk/typing/base/` package
- One file per model: `res_partner.py`, `res_users.py`, `res_company.py`, `res_country.py`, `res_country_state.py`, `res_currency.py`, `res_lang.py`, `ir_model.py`, `ir_model_fields.py`, `ir_attachment.py`, `ir_rule.py`, `ir_config_parameter.py`
- Tests in `tests/test_typing/`

Checklist
- [ ] All 12 models defined with correct `_odoo_model`, `_supported_versions = ("16.0", "17.0", "18.0", "19.0")`.
- [ ] Fields annotated with correct types and `OdooField(since=...)` for version-gated additions.
- [ ] `TypeRegistry` pre-registers all 12 models at import time.
- [ ] Unit tests for each model: instantiation succeeds, `supports_version` is correct, version-gated fields are stripped.

Done when
- All 12 pre-built models pass their unit tests and are registered in `TypeRegistry`.

PRD inputs captured by this item
- User-visible behavior change: `res.partner` records have a typed interface with no consumer setup.
- Main technical risk: field names and types may differ slightly across Odoo 16–19; verify against Odoo source.

## G7 - Documentation and Validation

Goal
- Update architecture docs, add examples, run full test suite.

Likely touch points
- `docs/odoo-sdk-architecture-plan.md`
- `examples/`
- `src/odoo_sdk/__init__.py`

Checklist
- [ ] Add an example demonstrating `recordset.read_typed()` for `res.partner`.
- [ ] Add an example demonstrating consumer `TypeRegistry` plugin registration.
- [ ] Update `docs/odoo-sdk-architecture-plan.md` with Phase G boundary and achievement summary.
- [ ] Export `OdooBaseModel`, `OdooField`, `TypeRegistry` from `src/odoo_sdk/__init__.py`.
- [ ] Full test suite passes with no regressions.
- [ ] Run with Pydantic installed and uninstalled; confirm graceful degradation.
- [ ] Mark all Phase G checklist items done.

Done when
- Typed models are validated in both Pydantic-present and Pydantic-absent environments.

## Detailed PRDs

```{toctree}
:maxdepth: 1
:glob:

phase-g/*
```
