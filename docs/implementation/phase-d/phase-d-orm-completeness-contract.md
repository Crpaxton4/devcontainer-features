# Phase D ORM Completeness Contract

## Purpose

This contract is the implementation baseline for Phase D.

Its job is to close the gap between the current SDK and idiomatic Odoo ORM usage by adding missing ORM methods, recordset functional operations, set operations, environment alterations, and domain composition helpers. Every Phase D task is evaluated against this document before it is accepted.

## Preserved Public Surfaces

The following public surfaces must remain usable throughout Phase D:

| Surface | Phase D status | Guardrail |
|---|---|---|
| `OdooClient` | Preserved | Remains the top-level facade and package story |
| `OdooModel` | Preserved | Remains usable as a proxy and compatibility wrapper |
| `OdooQuery` | Preserved | Remains usable as a transitional fluent compatibility shim |
| `OdooExecutor` | Preserved | Remains the execution seam for transport behavior |
| `OdooEnv` | Preserved and extended | Gains `with_user` and `with_company` |
| `OdooRecordset` | Preserved and extended | Gains all Phase D methods |
| `DomainExpression` | Preserved and extended | Gains composition helpers and operator support |
| `CommandDispatcher` | Preserved | No changes in Phase D |

## Responsibility Boundaries

| Abstraction | Gains in Phase D | Does not own |
|---|---|---|
| `OdooRecordset` | `_read_group`, `name_create`, `name_search`, `default_get`, `copy`, `get_metadata`, `filtered`, `mapped`, `sorted`, `grouped`, `filtered_domain`, set operators, `with_user`, `with_company`, `action_archive`, `action_unarchive` | Transport logic, reflection, schema validation |
| `OdooEnv` | `with_user`, `with_company` | Transport mutation, schema discovery |
| `DomainExpression` | `AND`, `OR`, `TRUE`, `FALSE`, `~`, `&`, `|`, dynamic time value pass-through | Server-side domain optimization, semantic rewriting |

## Resolved Phase D Decisions

### sudo() Exclusion

`sudo()` is explicitly excluded from Phase D and from the SDK entirely. Over the Odoo external API (both XML-RPC and JSON-2), there is no mechanism to escalate privileges in a call. Any `sudo()` implementation would be a silent no-op that misleads developers. The method is intentionally absent and its absence is documented here so it is not re-introduced.

### Functional Operations Are Client-Side

`filtered`, `mapped`, `sorted`, `grouped`, and `filtered_domain` are evaluated in-memory against already-fetched field values. They do not issue additional server calls. If field values are not yet cached, they are fetched first through the existing `read_adapted()` path. This mirrors Odoo ORM semantics where these operations operate on an already-hydrated recordset.

### with_user and with_company Create New Env/Recordset Instances

`with_user` and `with_company` follow the same derivation pattern as `with_context`: they return a new `OdooEnv` or `OdooRecordset` instance. They never mutate the existing env or executor. The underlying `OdooRpcExecutor` (or any future executor) must support a uid-override path without requiring a new authenticated session per call.

### _read_group Is the Canonical Aggregation Method

The deprecated `read_group` method (removed in Odoo 19) is not implemented. Only `_read_group` is implemented, matching the current Odoo API. Version-specific guidance is added to the method docstring.

### active_test Is a Context Key, Not a Filter

`active_test=False` in the context dictionary is passed through to the server and suppresses the automatic `active=False` filter Odoo applies by default. It is not applied client-side. `action_archive` and `action_unarchive` are simple `write` wrappers.

### Domain Composition Edge Cases

- `DomainExpression.AND([])` returns `DomainExpression.TRUE` (matches all records).
- `DomainExpression.OR([])` returns `DomainExpression.FALSE` (matches no records).
- `DomainExpression.AND([d])` and `DomainExpression.OR([d])` return the single domain unchanged.

### Synchronous Only

All Phase D operations are synchronous. No background threads, thread pools, or async variants are introduced.

## Explicitly Deferred Work

The following work is outside Phase D and must not be smuggled in:

- JSON-2 transport (Phase E)
- Runtime model reflection or schema discovery (Phase F)
- Pydantic model validation (Phase G)
- MCP integration (Phase H)
- `sudo()` support (explicitly excluded, not deferred)
- Async facade
- CI or release automation
