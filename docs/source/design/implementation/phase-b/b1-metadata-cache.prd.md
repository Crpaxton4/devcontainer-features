# Feature Name

Metadata Cache for `fields_get`

# Goal

## Problem

Phase B depends on field metadata to interpret Odoo responses and serialize richer write payloads correctly. If `fields_get` remains a direct call performed independently by each model, recordset, or adapter path, the SDK will become chatty, inconsistent, and harder to reason about under repeated reads. The cost is not just extra round trips; it is also the risk that different call sites make different assumptions about field types or relation targets. The SDK needs one stable cache boundary for metadata before broader field semantics are added.

## Solution

Introduce a shared in-memory metadata cache for `fields_get` results that is owned by the current local runtime boundary and reused across recordsets, models, and compatibility flows. The cache must normalize lookups by model name and requested field or attribute combinations, route misses through one retrieval path, and provide explicit miss, failure, and invalidation behavior. This creates the stable metadata dependency that Phase B adapters and serializers can build on without repeatedly hitting the wire.

# Requirements

## Functional Requirements

- The SDK must provide one dedicated metadata-cache component for `fields_get` behavior rather than embedding ad hoc memoization in model or query methods.
- The cache must be shared across derived recordsets and compatibility wrappers that operate within the same client or env-bound runtime, rather than being owned per recordset instance.
- The cache key must include at minimum the model name, requested field set, and requested attribute set, with deterministic normalization so equivalent requests reuse the same entry.
- The cache must define behavior for requests with omitted field lists, omitted attribute lists, and mixed ordering of requested inputs.
- Cache misses must route through one metadata retrieval path that issues `fields_get` through the existing execution boundary and stores the successful result for reuse.
- Failed metadata retrieval must not create a poisoned cache entry; repeated calls after a failure must retry retrieval unless the implementation explicitly documents a short-lived negative-cache policy.
- The cache must preserve raw metadata payload shape as returned by Odoo unless an explicitly documented metadata-normalization step is required for deterministic lookups.
- Phase B must define cache invalidation as a local, explicit, process-scoped operation; automatic cross-process or server-driven invalidation is deferred.
- The public or internal API must expose one intentional way to clear or bypass cached metadata when a maintainer needs fresh `fields_get` data during the current process.
- Recordset-centered and compatibility-centered code paths that need metadata must use this shared cache rather than calling `fields_get` directly.
- Local unit tests must cover cache hits, cache misses, key normalization, repeated access, explicit invalidation, and retrieval failures.

## Non-Functional Requirements

- Metadata caching must reduce repeated `fields_get` round trips without introducing hidden cross-process persistence.
- Cache behavior must be deterministic and easy to reason about in tests.
- The design must remain local-runtime friendly and must not require Redis, SQLite, or any other external cache store in Phase B.
- The cache component must stay narrow enough that future plugin or model-registry work can reuse it without redesigning its ownership boundary.

# Acceptance Criteria

- [ ] Repeated metadata lookups for the same model and equivalent field or attribute request reuse a cached result within the same runtime boundary.
- [ ] Requests that differ only by caller input ordering normalize to the same cache key.
- [ ] A metadata miss issues exactly one retrieval through the defined `fields_get` execution path before caching the successful result.
- [ ] A failed metadata lookup does not permanently poison the cache entry for future retries.
- [ ] A documented invalidation or bypass mechanism exists for maintainers who need fresh metadata in the current process.
- [ ] Unit tests cover hit, miss, invalidation, and failure behavior.

# Out of Scope

- Persistent metadata caching across process restarts.
- Automatic invalidation based on server-side schema changes.
- Record payload caching or prefetch behavior.
- Plugin-defined metadata providers.
