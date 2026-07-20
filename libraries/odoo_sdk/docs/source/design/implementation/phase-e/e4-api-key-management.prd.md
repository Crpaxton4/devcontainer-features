# Feature Name

API Key Management Helpers

> **Status: never implemented (2026-07 audit).** Neither `OdooClient.generate_api_key` nor `OdooClient.revoke_api_key` exists in `src/odoo_sdk/client/client.py`, and nothing in the source calls `res.users.apikeys`. API keys are consumed only — resolved from `ODOO_API_KEY` or the `[odoo]` INI section into `OdooConnectionSettings.api_key` and passed to `OdooJson2Executor` as a bearer token. Key rotation remains a manual, out-of-band operation. Retained as a record of the original Phase E plan.

# Goal

## Problem

JSON-2 API keys have finite lifetimes and must be rotated. Consumers doing programmatic key rotation must call `res.users.apikeys.generate` and `res.users.apikeys.revoke` via raw `execute` calls, bypassing the SDK's context handling and error mapping.

## Solution

Add `generate_api_key` and `revoke_api_key` as first-class methods on `OdooClient`, available only when the client uses JSON-2 transport.

# Requirements

## Functional Requirements

- `OdooClient.generate_api_key(scope: str | None, name: str, expiration_date: str) -> str` — calls `res.users.apikeys/generate` and returns the new key string; `scope` defaults to `None` (unscoped); `expiration_date` must be ISO 8601 format (e.g., `'2026-12-31'`).
- `OdooClient.revoke_api_key(key: str) -> None` — calls `res.users.apikeys/revoke` with the given key.
- Both methods must raise `NotImplementedError` when the client is backed by `OdooRpcExecutor`.
- Both methods must propagate `OdooAuthenticationError` if the request key is invalid.
- Method docstrings must document the bootstrapping requirement: you must authenticate with an existing key to generate a new one.
- Method docstrings must include the key rotation best practices: generate new → store securely → update services → revoke old.

## Non-Functional Requirements

- The key returned by `generate_api_key` must not be logged.
- Both methods are synchronous.

# Acceptance Criteria

- [ ] `client.generate_api_key(None, 'My service', '2026-12-31')` returns a string key.
- [ ] `client.revoke_api_key(old_key)` completes without error when the key is valid.
- [ ] Calling either method on an XML-RPC client raises `NotImplementedError`.
- [ ] An invalid key in `revoke_api_key` raises `OdooAuthenticationError`.
- [ ] Unit tests cover success and error cases with mocked executor.

# Out of Scope

- Key storage or secrets management.
- Automated key rotation scheduling.
- Listing existing API keys.
