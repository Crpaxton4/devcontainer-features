# Feature Name

Plugin Contracts and Extension Boundaries

> **Status: never implemented (2026-07 audit).** No part of Phase C shipped. `src/odoo_sdk/` contains no plugin contract, plugin registry, typed-adapter layer, or execution-policy seam for tracing, retry, timeout, or telemetry, and there is no async facade. The `adapters/` package is unrelated to this phase: it holds `state_persistence.py` and `external_sync.py` for the task-tracker. Retained as a record of the original Phase C plan. No plugin contract, protocol, or ABC was ever defined; `src/odoo_sdk/commands/protocols.py` is the command layer's typing surface, not a plugin seam.

# Goal

## Problem

Phase B introduces metadata and adaptation seams, but it still assumes that the SDK itself owns nearly all behavior. Once multiple consumers need model-specific behavior, custom serializers, or controlled extensions for internal use, ad hoc overrides will quickly become tempting. Without explicit plugin contracts, those extensions would spread through monkey-patching, subclassing, or compatibility-layer hacks that are hard to reason about and harder to test. The SDK needs a narrow, documented extension model that permits targeted customization without turning every internal object into an open interception surface.

## Solution

Define a small set of plugin contracts for model-specific behavior and clearly document where plugins are allowed to participate in SDK execution. The contracts should specify supported hook categories, typed inputs and outputs, failure behavior, and explicit no-go zones such as transport ownership or unrestricted mutation of core abstractions. This gives maintainers and consumers one stable extension model instead of many informal ones.

# Requirements

## Functional Requirements

- The implementation must define the minimum plugin hook categories needed in Phase C for model-specific behavior and targeted extension.
- The plugin contract must explicitly define where hooks are allowed to run, such as model-specific adaptation or serialization seams that already exist in the Phase A and Phase B architecture.
- The plugin contract must explicitly define where hooks are not allowed to run, including unrestricted transport replacement, arbitrary mutation of `OdooEnv`, domain serialization ownership, or direct takeover of recordset identity behavior.
- Plugin inputs must include enough stable context for safe execution, such as model identity, operation context, relevant metadata, and the typed payload shape expected by the hook category.
- Plugin outputs must be defined per hook category so maintainers can validate whether a plugin preserves the SDK contract.
- The implementation must define how plugin contracts are represented in Python, such as protocols, abstract base classes, or other typed callable contracts that are compatible with the repository's style.
- The contract must define how plugin failures are surfaced so they do not silently corrupt execution flow or fall back unpredictably.
- The contract must define how unsupported or mis-typed plugins are rejected during registration or validation.
- The contract must document whether hooks are pre-operation, post-operation, transformation-oriented, or selection-oriented for each supported category.
- Local unit tests must cover plugin contract validation, allowed hook execution, rejected hook misuse, and failure propagation behavior.

## Non-Functional Requirements

- The plugin surface must remain narrow and predictable.
- The design must favor explicit contracts over magic naming conventions or global side effects.
- The plugin model must be typed enough for maintainers to review compatibility without reading implementation internals.
- The design must avoid creating a framework-like extension system that is broader than the current product need.
- The contract must remain compatible with local-only testing and documentation workflows.

# Acceptance Criteria

- [ ] One documented plugin contract model exists for the supported Phase C hook categories.
- [ ] The documentation clearly distinguishes allowed extension points from prohibited ones.
- [ ] Plugin input and output expectations are defined clearly enough that invalid plugins can be rejected deterministically.
- [ ] Plugin failures produce predictable, test-covered behavior rather than silent corruption or accidental fallback.
- [ ] Tests prove that valid plugins can satisfy the contract and that invalid plugins are rejected or fail fast in a documented way.

# Out of Scope

- Automatic third-party package discovery or a plugin marketplace model.
- Arbitrary interception of every model, query, transport, or session call.
- Replacing the metadata cache, domain boundary, or recordset abstraction with plugin-owned logic.
- Hosted plugin distribution or remote configuration systems.
