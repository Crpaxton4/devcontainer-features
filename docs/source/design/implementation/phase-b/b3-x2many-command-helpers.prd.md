# Feature Name

X2Many Command Helper API

# Goal

## Problem

Odoo's x2many write protocol uses positional command tuples that are powerful but awkward to construct correctly from application code. Today, consumers would need to remember command integers, tuple shapes, and the distinction between operations like `delete`, `unlink`, `link`, `clear`, and `set`. That creates needless protocol exposure and makes write behavior harder to review or validate. Phase B should provide a clearer SDK path for common x2many operations without removing compatibility for callers that still pass raw tuples.

## Solution

Introduce a small x2many helper API that models the standard Odoo command tuple operations and serializes them through one shared normalization path before execution. The helper API should cover the core Odoo commands needed for create, update, delete, unlink, link, clear, and set behavior, while preserving acceptance of raw tuples for compatibility. This gives consumers an ergonomic write path and keeps the wire protocol centralized inside the SDK.

# Requirements

## Functional Requirements

- The SDK must define a dedicated x2many command helper abstraction rather than requiring callers to construct raw positional tuples manually.
- The helper API must cover the standard Odoo x2many command operations required for common writes: create, update, delete, unlink, link, clear, and set.
- Each helper operation must capture the minimum required payload for that operation and reject malformed input before XML-RPC execution.
- The SDK must provide one shared serializer that converts helper objects into the exact tuple payload shape expected by Odoo's write protocol.
- The write path must accept helper objects, raw tuples, or a documented mixture of both, but all supported inputs must be normalized through the same serialization boundary before execution.
- Serialization must preserve caller ordering for lists of x2many commands.
- The helper API must remain model-agnostic and must not require typed adapters or model-specific builder classes.
- The write path must document how x2many helper serialization interacts with ordinary scalar values in the same payload.
- Invalid helper inputs and malformed raw tuple inputs must fail predictably before or at the shared serialization boundary.
- Local unit tests must cover serialization of every supported helper operation, mixed helper and raw tuple input, invalid input handling, and write-path integration.

## Non-Functional Requirements

- The helper API must reduce protocol memorization without trying to redesign Odoo's write semantics.
- Serialization must be deterministic and transparent enough that maintainers can inspect the produced command tuples easily in tests.
- The design must stay small and explicit rather than introducing a fluent DSL for every x2many workflow.
- The implementation must preserve compatibility for existing raw tuple callers during Phase B.

# Acceptance Criteria

- [ ] Consumers can express create, update, delete, unlink, link, clear, and set operations through SDK helpers without manually building tuples.
- [ ] The serializer produces the exact Odoo-compatible tuple form for each supported helper operation.
- [ ] Raw tuple input remains accepted where compatibility requires it.
- [ ] Mixed helper and raw tuple command lists normalize through one shared serialization path.
- [ ] Invalid helper payloads fail predictably and are covered by tests.
- [ ] Unit tests cover helper serialization and write-path usage.

# Out of Scope

- A model-specific DSL for x2many writes.
- Automatic diffing between current relation state and desired relation state.
- Bulk write orchestration beyond serializing x2many payloads.
- Plugin-defined custom x2many command types.
