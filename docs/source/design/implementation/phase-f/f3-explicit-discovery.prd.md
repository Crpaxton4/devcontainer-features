# Feature Name

OdooModelRegistry — Explicit Discovery

# Goal

## Problem

Lazy fetch works well for ad hoc exploration, but high-volume integrations that touch many models at startup pay repeated first-access latency for each model. A single eager-loading call is more efficient for known model sets.

## Solution

Implement `registry.discover(models=None, fields=None)` that fetches all or a subset of model schemas in two bulk calls (`ir.model` and `ir.model.fields`) and populates the cache.

# Requirements

## Functional Requirements

- `registry.discover(models: list[str] | None = None, fields: list[str] | None = None, force: bool = False)` — synchronous; blocks until all schemas are loaded.
- When `models=None`: fetch all installed models from `ir.model` in a single call, then fetch all their fields from `ir.model.fields` in a second call.
- When `models=['res.partner', 'res.users']`: fetch only those models and their fields.
- When `fields=['name', 'email']`: after fetching models, filter `FieldSchema` objects to only include the named fields (plus required structural fields: `name`, `ttype`, `string`).
- When `force=False`: skip models already in the cache.
- When `force=True`: re-fetch all requested models even if cached.
- Returns `dict[str, ModelSchema]` mapping model names to schemas for all discovered models.

## Non-Functional Requirements

- The method is synchronous; it does not spawn threads.
- For large instances, document that providing a `models` list is recommended.

# Acceptance Criteria

- [ ] `registry.discover()` returns schemas for all installed models.
- [ ] `registry.discover(['res.partner'])` returns only `res.partner` schema.
- [ ] `registry.discover(['res.partner'], force=True)` re-fetches even if cached.
- [ ] After `discover()`, `registry.get('res.partner')` returns cached result without a server call.
- [ ] Unit tests cover full discover, partial discover, `force=True`, and post-discover cache hit.

# Out of Scope

- Async or background discovery.
- Streaming results during discovery.
