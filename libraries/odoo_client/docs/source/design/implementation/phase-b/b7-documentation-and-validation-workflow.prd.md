# Feature Name

Phase B Documentation and Local Validation Workflow

> **Status: partially superseded (2026-07 audit).** The Phase B semantics documentation requirements hold, but the locations and the workflow framing are stale: design docs live under `docs/source/design/` (Sphinx, built with `-W`), not a flat `docs/` tree, and validation runs in GitHub Actions rather than local tooling only. The compatibility-surface documentation requirement no longer applies — `OdooModel` and `OdooQuery` are absent from the shipped SDK.

# Goal

## Problem

Phase B introduces semantics that are more opinionated than the Phase A architectural scaffolding, which means the implementation can become difficult to use or review if the documentation lags behind the code. Maintainers need clear guidance on cache ownership, adapted-value boundaries, x2many helper behavior, error handling, and how to validate the phase locally. If those details are scattered or stale, future Phase C work will inherit ambiguity instead of a stable baseline. The repository needs one explicit documentation and validation-workflow task to keep the architecture plan, implementation docs, and local commands aligned.

## Solution

Update the Phase B documentation set so it describes the implemented metadata cache boundary, field-adaptation semantics, x2many helper behavior, error taxonomy, and local validation workflow in one coherent way. The workflow must document both the existing local unit-and-coverage path and the new live-Odoo validation path, while keeping the project local-tooling-only. This ensures the code, docs, and maintainer workflow all describe the same Phase B behavior before the project moves on to extensibility work in Phase C.

# Requirements

## Functional Requirements

- The Phase B documentation set must describe the implemented ownership boundary for metadata caching.
- The docs must describe the supported Phase B field categories and the boundary between raw extraction behavior and adapted semantic behavior.
- The docs must describe the x2many helper API at the level needed for maintainers and consumers to understand supported operations and serialization expectations.
- The docs must describe the Phase B error taxonomy, where mapping occurs, and how callers should handle broad versus specific SDK errors.
- The docs must describe how compatibility surfaces continue to work during Phase B and how they relate to recordset-first internals.
- The docs must describe the local environment-preparation path for Phase B validation from the repository root.
- The docs must describe the local unit-test and coverage path that remains the baseline validation command for the repository.
- The docs must describe the separate local live-Odoo validation path introduced for Phase B, including setup assumptions and opt-in or skip behavior.
- The architecture plan, design-pattern guidance, and Phase B implementation checklist must remain aligned with the behavior described in the Phase B PRDs and implementation docs.
- Any examples or implementation notes updated for Phase B must remain consistent with the repository's local-tooling-only workflow.

## Non-Functional Requirements

- Documentation must optimize for maintainers making Phase B changes or reviewing them locally.
- The validation workflow must stay concise, scriptable, and free of CI or hosted-service dependencies.
- Documentation changes must avoid inventing broader Phase C extension stories before those seams are implemented.
- The written guidance must be precise enough that a later PRD author can use Phase B docs as a stable baseline.

# Acceptance Criteria

- [ ] Phase B documentation describes the implemented cache boundary, adapted field categories, x2many helper behavior, and error taxonomy.
- [ ] The docs explain how Phase B semantics coexist with preserved raw extraction and compatibility paths.
- [ ] The local environment-preparation command is documented for maintainers.
- [ ] The local unit-test and coverage validation command remains documented as the baseline repository check.
- [ ] The local live-Odoo validation path is documented separately with setup assumptions and opt-in or skip behavior.
- [ ] The architecture plan, design-pattern notes, and implementation checklist remain consistent with the implemented Phase B behavior.

# Out of Scope

- CI pipelines or hosted release workflows.
- Package publishing documentation.
- Full user-facing migration guides for future Phase C extension APIs.
- Rewriting documentation unrelated to Phase B semantics or validation.
