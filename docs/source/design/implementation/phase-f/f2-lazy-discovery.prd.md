# Feature Name

OdooModelRegistry — Lazy Discovery

# Goal

## Problem

The SDK has no way to enumerate or validate model schemas without manually calling `ir.model` and `ir.model.fields`. Every consumer that needs this information duplicates the same queries. There is no caching, so repeated schema lookups are unnecessarily expensive.

## Solution

Implement `OdooModelRegistry` with lazy discovery: fetch a model's schema from `ir.model` and `ir.model.fields` on first access, then cache the `ModelSchema` for subsequent calls.

# Requirements

## Functional Requirements

- `OdooModelRegistry(env: OdooEnv)` constructor.
- `registry.get(model_name: str) -> ModelSchema` — on first call for a model, queries `ir.model` and `ir.model.fields` via the env's executor; caches and returns the result; on subsequent calls, returns the cached result.
- `registry.__contains__(model_name: str) -> bool` — returns `True` if the model is installed; fetches from server if not yet cached.
- `registry.__getitem__(model_name: str) -> ModelSchema` — alias for `get`; raises `OdooMissingRecordError` if the model does not exist.
- Thread-safe: concurrent first-access for the same model fetches once (not N times) using a per-model lock.

## Non-Functional Requirements

- The constructor must not issue any server calls.
- Schema fetch is synchronous and blocks the caller.
- The internal cache is a `dict[str, ModelSchema]` protected by a `threading.Lock`.

# Acceptance Criteria

- [ ] First `registry.get('res.partner')` fetches from `ir.model` and `ir.model.fields`.
- [ ] Second `registry.get('res.partner')` returns the cached result without a server call.
- [ ] `'res.partner' in registry` returns `True`.
- [ ] `'nonexistent.model' in registry` returns `False`.
- [ ] `registry['nonexistent.model']` raises `OdooMissingRecordError`.
- [ ] Concurrent first-access for the same model does not result in duplicate server calls.
- [ ] Unit tests cover first access, cache hit, missing model, and concurrent access.

# Out of Scope

- TTL expiry (Phase F4).
- Explicit bulk discovery (Phase F3).
- Field validation on recordset operations.
