# Feature Name

OdooEnv Integration

# Goal

## Problem

Without env integration, Phase G and Phase H must construct their own `OdooModelRegistry` instances from scratch. This duplicates the cache and means two separate instances may have inconsistent schema views.

## Solution

Add `env.registry` as a lazy property that returns a shared `OdooModelRegistry` instance. Derived envs share the same registry, and `env.get_model_schema(model_name)` provides a convenience accessor.

# Requirements

## Functional Requirements

- `env.registry -> OdooModelRegistry` — lazy property; initialized once on first access; the same instance is returned on all subsequent accesses.
- `env.get_model_schema(model_name: str) -> ModelSchema` — convenience method that delegates to `env.registry.get(model_name)`.
- When `with_context`, `with_user`, or `with_company` derive a new `OdooEnv`, the derived env shares the parent's `OdooModelRegistry` instance (passed by reference, not re-created).
- The registry instance is owned by the root env and passed down to all derived envs.

## Non-Functional Requirements

- `env.registry` must not issue any server calls on instantiation; it delegates to `OdooModelRegistry`, which is lazy.

# Acceptance Criteria

- [ ] `env.registry` returns the same instance on every call.
- [ ] `env.get_model_schema('res.partner')` returns a `ModelSchema`.
- [ ] `env.with_context(lang='fr').registry` is the same object as `env.registry`.
- [ ] `env.with_user(uid).registry` is the same object as `env.registry`.
- [ ] Unit tests confirm registry identity is preserved across all env derivation methods.

# Out of Scope

- Field validation wired to the registry (Phase G).
- MCP resource serving using the registry (Phase H).
