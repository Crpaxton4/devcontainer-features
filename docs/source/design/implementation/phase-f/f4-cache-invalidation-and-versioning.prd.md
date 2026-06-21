# Feature Name

Cache Invalidation and Version Fingerprinting

# Goal

## Problem

When an Odoo module is installed or upgraded, the live schema changes. A stale `OdooModelRegistry` cache produces subtle bugs: unknown fields are accessed, new required fields are missed, and deleted fields cause server errors. The registry must detect schema changes and invalidate the relevant portions.

## Solution

Implement explicit `registry.invalidate()`, TTL-based expiry, and a version fingerprint derived from the server version and installed module list. If the fingerprint changes, the entire cache is invalidated.

# Requirements

## Functional Requirements

- `registry.invalidate(model_name: str | None = None)` — clears the cache for a single model (when `model_name` is provided) or all models (when `None`).
- TTL: each cached `ModelSchema` records its fetch timestamp; schemas older than `ttl_seconds` (default: 3600) are considered stale and re-fetched on next access.
- `OdooModelRegistry(env, ttl_seconds=3600)` — TTL is configurable at construction time.
- Version fingerprint: on first use, the registry computes `fingerprint = hash(server_version() + sorted(installed_module_names))` and stores it.
- On each `get()` call, the stored fingerprint is compared to the live fingerprint. If changed, the entire cache is invalidated before returning the schema.
- `registry.version_fingerprint -> str` — read-only property exposing the current fingerprint for diagnostics.
- The fingerprint check itself is cached with its own TTL (`fingerprint_check_ttl_seconds=300`) to avoid an `ir.module.module` query on every single `get()` call.

## Non-Functional Requirements

- Fingerprint computation is synchronous.
- All invalidation operations are thread-safe.

# Acceptance Criteria

- [ ] `registry.invalidate('res.partner')` removes only `res.partner` from the cache.
- [ ] `registry.invalidate()` clears all cached schemas.
- [ ] Schema older than TTL is re-fetched on next `get()`.
- [ ] Fingerprint change triggers full cache invalidation.
- [ ] `registry.version_fingerprint` returns a non-empty string.
- [ ] Unit tests cover TTL expiry, explicit invalidation, and fingerprint-triggered invalidation.

# Out of Scope

- Automatic background refresh.
- Per-field cache granularity.
