# Phase F Implementation Checklist

> **Status: never implemented (2026-07 audit).** No part of Phase F shipped. There is no `OdooModelRegistry`, `ModelSchema`, or `FieldSchema` anywhere in `src/odoo_sdk/`, and no `ir.model` / `ir.model.fields` reflection path. The only metadata layer that shipped is the Phase B `MetadataCache` over `fields_get` (`src/odoo_sdk/env/metadata_cache.py`), which this phase was meant to sit beside. `OdooEnv`, which every Phase F surface below hangs off, was itself removed in PR #161. Retained as a record of the original Phase F plan.

## Objective

Introduce runtime model reflection by querying `ir.model` and `ir.model.fields` on the connected Odoo instance. Phase F adds an `OdooModelRegistry` that caches schema information (model names, field definitions, types, constraints) and integrates into `OdooEnv`. Discovery is lazy by default and always synchronous.

## PRD-Ready Context

### Problem statement

The SDK currently assumes models and fields exist and relies on the programmer to ensure correctness. Passing an invalid field name to `search` or `write` produces a server-side error with an opaque message. There is no way to enumerate what models or fields exist without manually calling `fields_get` and interpreting the result. Phase F changes this by giving the SDK a live, cached view of the connected Odoo instance's schema.

### Desired outcome

- `OdooModelRegistry` exists and caches `ModelSchema` and `FieldSchema` objects.
- Discovery is lazy by default: a model's schema is fetched the first time that model is accessed.
- `registry.discover(models=None, fields=None)` allows explicit eager loading of all or a subset of models and fields.
- A version fingerprint (Odoo server version + module hash) detects when the schema may have changed and triggers re-discovery.
- `OdooEnv` exposes `env.registry` and `env.get_model_schema(model_name)`.
- Phase G (Pydantic) and Phase H (MCP) both build on Phase F schema objects.

### Non-goals

- No Pydantic model generation (Phase G).
- No MCP integration (Phase H).
- No async discovery.
- No CI or release automation.

### Constraints

- All discovery is synchronous.
- The registry uses `ir.model` and `ir.model.fields` via the existing executor (works with both XML-RPC and JSON-2).
- No new external dependencies.
- Preserve all Phase A–E public surfaces.

### Success signal

- `env.get_model_schema('res.partner')` returns a complete `ModelSchema` with all field definitions.
- `registry.discover()` loads all installed models in a single session.
- Cache invalidation via `registry.invalidate()` forces re-fetch on next access.
- Version fingerprint change triggers automatic invalidation.
- Unit tests cover lazy fetch, explicit discovery, TTL, invalidation, and version fingerprint change.

## Execution Order

1. Lock down Phase F boundaries and reflection contract.
2. Define `ModelSchema` and `FieldSchema` dataclasses.
3. Implement lazy discovery in `OdooModelRegistry`.
4. Implement explicit `discover()` method.
5. Implement cache invalidation and version fingerprinting.
6. Integrate registry into `OdooEnv`.
7. Update docs, examples, and local validation.

## Implementation Checklist

## F0 - Phase Guardrails

Goal
- Define the exact Phase F contract before reflection work begins.

Likely touch points
- `docs/implementation/phase-f/phase-f-reflection-contract.md`
- `docs/implementation/phase-f-implementation-checklist.md`
- `docs/odoo-sdk-architecture-plan.md`

Checklist
- [ ] Create and adopt a dedicated Phase F reflection contract.
- [ ] Confirm that reflection uses only `ir.model` and `ir.model.fields` via the existing executor.
- [ ] Confirm lazy-by-default, synchronous-only discovery.
- [ ] Confirm `ModelSchema` and `FieldSchema` as the schema representation types.
- [ ] Confirm that the existing `MetadataCache` (`fields_get`) and the new `OdooModelRegistry` (`ir.model`) are complementary, not redundant.
- [ ] Confirm which work is deferred to Phase G and Phase H.

Done when
- F1–F6 PRD authors can validate their tasks against the contract.

## F1 - ModelSchema and FieldSchema Dataclasses

Goal
- Define the data structures that represent a model's schema as returned by `ir.model` and `ir.model.fields`.

Why this exists
- A well-defined schema representation is the shared language for Phase F (reflection), Phase G (Pydantic), and Phase H (MCP tools). Without it, each phase builds its own ad hoc structure.

Likely touch points
- New `src/odoo_sdk/reflection/schema.py`
- Tests in `tests/test_reflection/`

Checklist
- [ ] Define `FieldSchema` dataclass with fields: `name`, `ttype`, `string`, `required`, `readonly`, `store`, `compute`, `relation`, `selection`, `domain`, `help`.
- [ ] Define `ModelSchema` dataclass with fields: `name`, `description`, `state`, `fields: dict[str, FieldSchema]`.
- [ ] Both are immutable (frozen dataclasses or `__slots__` with no setters).
- [ ] `FieldSchema.is_relational` property returns `True` for `many2one`, `one2many`, `many2many`.
- [ ] `FieldSchema.is_computed` property returns `True` when `compute` is not `None`.
- [ ] Add unit tests for each property.

Done when
- `ModelSchema` and `FieldSchema` are defined, tested, and available for use in F2–F5.

PRD inputs captured by this item
- User-visible behavior change: schema data has a typed representation.
- Main technical risk: `ir.model.fields` field names differ slightly across Odoo versions; the dataclass must handle missing fields gracefully with defaults.

## F2 - Lazy Discovery

Goal
- Implement the lazy schema fetch in `OdooModelRegistry` so each model's schema is fetched on first access and cached.

Why this exists
- Fetching all model schemas on connection startup would be slow for instances with hundreds of installed modules. Lazy fetch keeps startup instant and lets the cache warm naturally as models are used.

Likely touch points
- New `src/odoo_sdk/reflection/registry.py`
- `src/odoo_sdk/env/env.py`
- Tests in `tests/test_reflection/`

Checklist
- [ ] Implement `OdooModelRegistry(env)` constructor.
- [ ] Implement `registry.get(model_name) -> ModelSchema` — fetches from `ir.model` + `ir.model.fields` on first access; returns cached result on subsequent calls.
- [ ] Implement `registry.__contains__(model_name) -> bool` — returns `True` if the model is installed, fetching if needed.
- [ ] Thread-safe cache access (read lock for hits, write lock for misses).
- [ ] Raise `OdooMissingRecordError` when `model_name` is not an installed model.
- [ ] Add unit tests for first-access fetch, subsequent cache hit, and missing model.

Done when
- Model schemas are fetched lazily on first access and cached for subsequent use.

PRD inputs captured by this item
- User-visible behavior change: schema access is transparent and does not require explicit discovery calls.
- Main technical risk: thread-safety for concurrent access without a global lock that bottlenecks all reads.

## F3 - Explicit Discovery

Goal
- Implement `registry.discover(models=None, fields=None)` for eager loading of all or a subset of schemas.

Why this exists
- High-volume or performance-sensitive integrations benefit from warming the cache at startup with a known model set rather than paying per-model fetch latency during execution.

Likely touch points
- `src/odoo_sdk/reflection/registry.py`
- Tests in `tests/test_reflection/`

Checklist
- [ ] Implement `registry.discover(models: list[str] | None = None, fields: list[str] | None = None)` — fetches schemas for the given models (or all installed models if `None`); field names act as a filter for `FieldSchema` attributes returned.
- [ ] `discover()` with no arguments fetches all models from `ir.model` in a single call, then fetches all fields from `ir.model.fields` in a second call.
- [ ] `discover(models=['res.partner', 'res.users'])` fetches only those two models and their fields.
- [ ] Already-cached models are skipped unless `force=True` is passed.
- [ ] The method is synchronous and blocks until all schemas are loaded.
- [ ] Add unit tests for full discovery, partial discovery, and `force=True` re-discovery.

Done when
- Callers can pre-warm the schema cache at startup with a single call.

PRD inputs captured by this item
- User-visible behavior change: startup-time schema warming is explicit and controllable.
- Main technical risk: full discovery on large Odoo instances may be slow; document this and recommend providing a `models` list for production use.

## F4 - Cache Invalidation and Version Fingerprinting

Goal
- Implement TTL-based expiry, explicit invalidation, and version fingerprinting so the registry stays consistent when Odoo modules are installed or upgraded.

Why this exists
- A cached schema that does not reflect the live instance produces subtle bugs: fields that no longer exist, new required fields that are missing, etc. Invalidation and fingerprinting prevent stale schema data.

Likely touch points
- `src/odoo_sdk/reflection/registry.py`
- `src/odoo_sdk/client/client.py` (`server_version()` from Phase E)
- Tests in `tests/test_reflection/`

Checklist
- [ ] Implement `registry.invalidate(model_name: str | None = None)` — clears the cache for a single model or all models.
- [ ] Implement TTL: cached schemas older than a configurable `ttl_seconds` (default: 3600) are re-fetched on next access.
- [ ] Implement version fingerprint: on registry creation, capture `server_version()` + installed module list hash; if the fingerprint changes between calls, invalidate the entire cache automatically.
- [ ] Expose `registry.version_fingerprint -> str` for diagnostics.
- [ ] Add unit tests for TTL expiry, explicit invalidation, and fingerprint change triggering invalidation.

Done when
- Stale schema data is detected and cleared reliably.

PRD inputs captured by this item
- User-visible behavior change: schema cache is self-healing after module installs.
- Main technical risk: computing the module list hash requires a `ir.module.module` query; this adds one extra call on registry creation.

## F5 - OdooEnv Integration

Goal
- Expose the `OdooModelRegistry` through `OdooEnv` so all recordset operations can optionally validate field names against the live schema.

Why this exists
- Without env integration, Phase G and Phase H must construct their own registry instances. Centralizing the registry on the env keeps the cache shared and consistent.

Likely touch points
- `src/odoo_sdk/env/env.py`
- `src/odoo_sdk/records/recordset.py`
- Tests in `tests/test_env/`

Checklist
- [ ] Add `env.registry -> OdooModelRegistry` property (lazy-initialized on first access).
- [ ] Add `env.get_model_schema(model_name) -> ModelSchema` convenience method.
- [ ] Registry is shared across all derived envs (`with_context`, `with_user`, `with_company`) from the same client.
- [ ] Registry is not duplicated when a new env is derived.
- [ ] Add unit tests for registry sharing across derived envs.

Done when
- `env.registry` returns the shared `OdooModelRegistry` and derived envs share the same cache.

PRD inputs captured by this item
- User-visible behavior change: schema access is available anywhere an env is available.
- Main technical risk: ensuring the registry is not inadvertently duplicated when envs are derived.

## F6 - Documentation and Validation

Goal
- Update all phase documentation and examples; validate reflection against a live Odoo instance.

Likely touch points
- `docs/odoo-sdk-architecture-plan.md`
- `examples/`
- `src/odoo_sdk/__init__.py`

Checklist
- [ ] Add an `examples/` script demonstrating `env.get_model_schema('res.partner')` and iterating its fields.
- [ ] Add an `examples/` script demonstrating `registry.discover(['res.partner', 'res.users'])`.
- [ ] Update `docs/odoo-sdk-architecture-plan.md` with Phase F boundary and achievement summary.
- [ ] Export `OdooModelRegistry`, `ModelSchema`, `FieldSchema` from `src/odoo_sdk/__init__.py`.
- [ ] Run full test suite; confirm no Phase A–E regressions.
- [ ] Run live smoke tests validating schema correctness against a known Odoo instance.
- [ ] Mark all Phase F checklist items done.

Done when
- Schema reflection is validated against a live instance and documentation is current.

## Detailed PRDs

```{toctree}
:maxdepth: 1
:glob:

phase-f/*
```
