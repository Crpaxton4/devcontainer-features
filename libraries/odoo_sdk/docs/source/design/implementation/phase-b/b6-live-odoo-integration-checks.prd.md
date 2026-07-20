# Feature Name

Local Live-Odoo Integration Checks for Phase B

> **Status: partially superseded (2026-07 audit).** No dedicated live-check suite shipped, and there is no live-check target in the `Makefile`. Live validation is done by hand through the reference scripts under `examples/general/`, which gate live runs behind an explicit `--allow-live-production` flag. The unit suite runs in GitHub Actions (`.github/workflows/odoo-sdk-quality.yaml`), which the 'no CI' framing below predates. The x2many write check described here would need care today: `unlink` is blocked at the single transport guard (`src/odoo_sdk/transport/errors.py`).

# Goal

## Problem

Phase B introduces metadata-aware behavior and richer semantic translation, which raises the risk that a mock-only test suite will validate internal intent but miss real Odoo behavior. Field metadata shape, relation payloads, datetime formatting, and XML-RPC fault strings all depend on the server and can drift across real environments. Without a scriptable live-Odoo check path, maintainers will not have a reliable way to confirm that the new semantics still match an actual Odoo instance before review. The project needs a local integration baseline that is strong enough to catch semantic drift without introducing hosted infrastructure.

## Solution

Add a local-only live-Odoo validation path that exercises the key Phase B semantics against a configured Odoo instance and can be run from the repository root. The checks must cover metadata retrieval, at least one adapted relation field, at least one normalized date or datetime field, x2many serialization, and mapped failure behavior where practical. The workflow must remain optional for unconfigured environments, but deterministic and documented for maintainers who do have local or shared development Odoo access. The default `project.task` smoke path should use live `fields_get` metadata to decide whether `date_deadline` round-trips as a Python `date` or `datetime`, so the validation follows the connected instance instead of assuming a fixed schema in advance.

# Requirements

## Functional Requirements

- The repository must provide a documented local integration-check path for Phase B that can run against at least one live Odoo instance.
- The live-check path must rely on repository-local tooling and configuration rather than CI or hosted infrastructure.
- The workflow must document the required connection inputs for a live run, including the currently used Odoo connection environment variables or an approved equivalent local config source.
- The live checks must cover successful metadata retrieval through the Phase B metadata path.
- The live checks must cover at least one relation field whose payload is adapted through the Phase B adaptation path.
- The live checks must cover at least one date or datetime field whose payload is normalized through the Phase B adaptation path.
- The live checks must cover x2many command serialization against a real write path or another safe live validation mechanism that proves the produced payload is accepted by Odoo.
- The live checks must cover at least one mapped error scenario where practical, such as auth, validation, or missing-record behavior, without requiring unsafe destructive operations.
- The live-check suite must define how it behaves when live Odoo credentials are not configured, such as a documented skip path rather than a hard failure for ordinary unit-test runs.
- The live checks must avoid unsafe or irreversible data mutations unless the test setup uses isolated data and documented cleanup behavior.
- The repository docs must include the exact command path for running the live checks from the repository root.

## Non-Functional Requirements

- The live-validation workflow must remain local-only and scriptable.
- The checks must be narrow enough to run during maintainer validation without becoming a full end-to-end test platform.
- The workflow must minimize environment-specific assumptions and document any required model or fixture expectations clearly.
- The design must preserve ordinary unit-test determinism by keeping live checks separate or explicitly skippable when no live environment is configured.
- The default `project.task` smoke path must remain metadata-driven and avoid hard-coded assumptions about whether `date_deadline` is a `date` or `datetime` field on a given instance.

# Acceptance Criteria

- [ ] A maintainer with valid Odoo credentials can run a documented local command that exercises the key Phase B semantics against a live instance.
- [ ] The live checks validate metadata retrieval, relation adaptation, date or datetime normalization, x2many command acceptance, and at least one mapped error path where practical.
- [ ] The workflow documents the required local configuration inputs.
- [ ] The live checks have a documented skip or opt-in behavior for environments without Odoo credentials.
- [ ] The workflow does not require CI, hosted runners, or external test orchestration.
- [ ] The live checks avoid unsafe irreversible mutations or document cleanup requirements clearly.

# Out of Scope

- Hosted integration environments.
- CI-enforced live integration testing.
- Broad end-to-end scenario coverage across many Odoo modules.
- Performance benchmarking against a live Odoo server.
