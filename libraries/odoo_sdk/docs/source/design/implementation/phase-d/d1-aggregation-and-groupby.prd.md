# Feature Name

Aggregation and GroupBy — `_read_group`

> **Status: partially superseded (2026-07 audit).** `OdooRecordset._read_group` shipped with the signature below (`src/odoo_sdk/records/recordset.py`), but it calls Odoo's *public* `read_group` XML-RPC method, not the internal `_read_group` ORM method, which is not reachable over XML-RPC. Consequently `having` is **not** supported: a non-empty `having` domain raises `NotImplementedError`. The granularity and aggregator specifiers listed below are passed through to the server rather than validated client-side, so the supported set is the server's, not the SDK's.

# Goal

## Problem

The SDK has no way to perform server-side aggregation. Integrations that need counts, sums, averages, or date-bucketed groupings must fetch all matching records and aggregate client-side, which is impractical for large datasets and produces incorrect results when the server applies access rules that filter records.

## Solution

Implement `_read_group` on `OdooRecordset`, mirroring the Odoo ORM `_read_group` signature. The method delegates to `execute_kw` and returns the raw grouped result list. The deprecated `read_group` (removed Odoo 19) is not implemented.

# Requirements

## Functional Requirements

- `OdooRecordset._read_group(domain, groupby=(), aggregates=(), having=(), offset=0, limit=None, order=None)` must exist.
- `domain` must accept a list, `DomainExpression`, or `None`.
- `groupby` must accept a sequence of field name strings or `'field:granularity'` strings. Supported granularities: `day`, `week`, `month`, `quarter`, `year`, `year_number`, `quarter_number`, `month_number`, `iso_week_number`, `day_of_week`, `day_of_month`, `day_of_year`, `hour_number`, `minute_number`, `second_number`.
- `aggregates` must accept a sequence of `'field:agg'` strings. Supported aggregators: `sum`, `avg`, `min`, `max`, `count`, `count_distinct`, `bool_and`, `bool_or`, `recordset`.
- `having` must accept a domain list or `DomainExpression` applied to aggregate results.
- The return value must be a list of tuples matching the Odoo `_read_group` response shape.
- When `aggregates` includes `'field:recordset'`, the returned value for that field must be an `OdooRecordset` instance for the related model.

## Non-Functional Requirements

- The method must not cache aggregation results.
- The method must be synchronous.
- Granularity and aggregator strings are passed through to the server without SDK-side validation, to remain forward-compatible with new Odoo versions.

# Acceptance Criteria

- [ ] `_read_group` exists on `OdooRecordset` with the documented signature.
- [ ] Calling `_read_group` with a groupby field returns one result group per distinct value.
- [ ] Calling `_read_group` with an aggregate returns the correct aggregate value per group.
- [ ] Combining groupby and aggregates in one call returns both group keys and aggregate values in each result tuple.
- [ ] The `having` parameter filters result groups correctly.
- [ ] An empty domain returns all records grouped.
- [ ] Unit tests cover groupby-only, aggregate-only, combined, having-filtered, and empty-result cases.
- [ ] An example in `examples/` demonstrates a realistic aggregate query.

# Out of Scope

- Implementing the deprecated `read_group` method.
- Client-side aggregation of fetched records.
- Caching or memoizing aggregation results.
