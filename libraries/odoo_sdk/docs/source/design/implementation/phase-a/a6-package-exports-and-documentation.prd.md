# Feature Name

Phase A Package Exports and Documentation Alignment

> **Status: superseded (2026-07 audit).** The export decision recorded here no longer matches `src/odoo_sdk/__init__.py`. `DomainExpression` and `OdooRecordset` are supported public exports rather than internal primitives; `OdooEnv` no longer exists (PR #161); and `OdooModel`, `OdooQuery`, and `CommandDispatcher` are absent. The shipped `__all__` is centred on `OdooClient`, `OdooRecordset`, `Domain`/`DomainExpression`, `Record`, `Command`/`Registry`, the executors, the error taxonomy, and `OdooMCPServer` (resolved lazily via PEP 562). The docs referenced below now live under `docs/source/design/`, not a flat `docs/implementation/` tree.

# Goal

## Problem

Phase A introduces new architectural concepts, but those concepts will be hard to use and maintain if the package exports and the written documentation do not match the implementation. The repo currently documents the architectural direction separately from the code surface, and the Phase A PRD set still needs a final export and documentation alignment pass under `docs/implementation/phase-a/`. Without explicit export and documentation work, maintainers could accidentally ship hidden APIs, stale docs, or conflicting statements about whether `OdooQuery` remains core or only transitional.

## Solution

Make the export status of `OdooEnv`, `DomainExpression`, and `OdooRecordset` an explicit Phase A decision, then update package exports and architecture documentation to match. The documentation must explain how the new abstractions relate to `OdooClient`, `OdooModel`, and `OdooQuery`, what compatibility promises are in force during Phase A, and which capabilities remain deferred until later phases.

# Requirements

## Functional Requirements

- The implementation must explicitly decide whether `OdooEnv`, `DomainExpression`, and `OdooRecordset` are public exports at Phase A completion.
- If a new abstraction is declared public, it must be exported consistently from the appropriate package `__init__` modules and documented as a supported Phase A surface.
- If a new abstraction is not declared public yet, the docs must state that it exists for Phase A architecture but is not yet a supported top-level public API.
- Package export decisions must keep the current top-level facade story centered on `OdooClient`.
- The architecture plan must be updated to reflect the implemented Phase A behavior rather than only the aspirational target architecture.
- The design-pattern documentation must reflect the final Phase A ownership boundaries and the compatibility role of `OdooModel` and `OdooQuery`.
- The Phase A documentation set must explain how `OdooEnv`, `DomainExpression`, and `OdooRecordset` relate to preserved entry points.
- Documentation must explicitly list Phase A compatibility promises and deferred decisions for later phases.
- Local examples may be updated only if needed to remove ambiguity, but example expansion is not required for Phase A completion.

## Non-Functional Requirements

- Documentation and package exports must tell the same story.
- The written guidance must be concise enough for maintainers to use during implementation review.
- The docs must remain local-workflow oriented and must not assume CI or release automation.
- Export decisions must avoid prematurely widening the public API beyond what the implementation can support with confidence.

# Acceptance Criteria

- [ ] The export status of `OdooEnv`, `DomainExpression`, and `OdooRecordset` is explicit and reflected consistently in package `__all__` declarations and documentation.
- [ ] The architecture plan and design-pattern guide describe the same Phase A architecture that the code implements.
- [ ] The documentation explains the relationship between `OdooClient`, `OdooModel`, `OdooQuery`, `OdooEnv`, `DomainExpression`, and `OdooRecordset`.
- [ ] The documentation explicitly calls out Phase A compatibility guarantees and deferred work.
- [ ] Phase A implementation PRDs exist under `docs/implementation/phase-a/` for all checklist items.

# Out of Scope

- A full example or tutorial rewrite.
- Release notes, publishing workflow, or packaging automation.
- Phase B or Phase C documentation beyond explicit deferment notes.
- Introducing public API exports for abstractions that are not stable enough for supported use.
