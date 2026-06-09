# Feature Name

JSON-2 HTTP and JSON Error Mapping

# Goal

## Problem

JSON-2 error responses carry both an HTTP status code and a JSON body with a Python exception class name. Without a consistent mapping from these to SDK error classes, consumers face different exception types from JSON-2 vs XML-RPC for the same logical failure.

## Solution

Extend the existing error mapping logic in `errors.py` to handle JSON-2 error bodies. Use the JSON `name` field (Python exception class name) as the primary discriminator, with HTTP status code as a fallback.

# Requirements

## Functional Requirements

- Parse the JSON error body to extract `name`, `message`, `arguments`, and `debug` fields.
- Map `name` values to SDK errors:
  - `odoo.exceptions.AccessDenied` or HTTP 401 → `OdooAuthenticationError`
  - `odoo.exceptions.AccessError` or HTTP 403 → `OdooAccessError`
  - `odoo.exceptions.MissingError` or HTTP 404 → `OdooMissingRecordError`
  - `odoo.exceptions.ValidationError` or HTTP 422 → `OdooValidationError`
  - `odoo.exceptions.UserError` → `OdooServerError` with user-facing message
  - Any other `name` or HTTP 5xx → `OdooServerError`
- When the response body is not valid JSON (e.g., nginx 502 HTML), raise `OdooTransportError` with the raw response body truncated to 500 characters.
- Preserve the `message` and `debug` fields in the raised exception for diagnostics.
- The `api_key` must not appear in any exception message.

## Non-Functional Requirements

- Error mapping must be a pure function that takes a status code and response body string and returns an exception instance.
- The mapping function must be usable from both `OdooJson2Executor` and any future transport without duplication.

# Acceptance Criteria

- [ ] HTTP 401 response raises `OdooAuthenticationError`.
- [ ] HTTP 403 response raises `OdooAccessError`.
- [ ] JSON body with `name: "odoo.exceptions.MissingError"` raises `OdooMissingRecordError`.
- [ ] JSON body with `name: "odoo.exceptions.ValidationError"` raises `OdooValidationError`.
- [ ] JSON body with an unknown `name` raises `OdooServerError`.
- [ ] Non-JSON body raises `OdooTransportError`.
- [ ] `message` is preserved in the raised exception.
- [ ] Unit tests cover each mapping path and the non-JSON fallback.

# Out of Scope

- Mapping Odoo warning types to non-error return values.
- Parsing the `debug` traceback for structured analysis.
