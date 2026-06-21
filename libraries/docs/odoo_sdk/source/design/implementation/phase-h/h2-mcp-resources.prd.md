# Feature Name

MCP Resources

# Goal

## Problem

Without resources, an LLM agent cannot discover which Odoo models are available or introspect their fields without prior knowledge. Resources provide the schema self-documentation layer.

## Solution

Implement three MCP resource handlers for model enumeration, schema introspection, and single-record fetching.

# Requirements

## Functional Requirements

- Resource `odoo://models`:
  - Returns a JSON list of objects with `name` and `description` for every installed Odoo model.
  - Uses Phase F `env.registry.discover()` when Phase F is available; otherwise queries `ir.model` directly.
  - Paginated: accepts optional `limit` and `offset` query parameters.

- Resource `odoo://model/{name}/schema`:
  - Returns a JSON object with `model`, `description`, and `fields` dict.
  - Each field entry includes: `string` (human label), `ttype`, `required`, `readonly`, `store`, `help`.
  - Uses Phase F `ModelSchema` when available; falls back to `fields_get` otherwise.
  - Returns structured JSON error if the model does not exist.

- Resource `odoo://model/{name}/records/{id}`:
  - Returns a JSON object for the record at `{id}` using all stored fields.
  - Returns structured JSON error if the record does not exist.

## Non-Functional Requirements

- All resource handlers are synchronous SDK calls wrapped in MCP async handlers.
- Resource errors must not crash the MCP server; return structured JSON.

# Acceptance Criteria

- [ ] `odoo://models` returns a list with at least `res.partner`.
- [ ] `odoo://model/res.partner/schema` returns a dict with a `fields` key containing `name` and `email`.
- [ ] `odoo://model/res.partner/records/1` returns a dict with `id: 1`.
- [ ] `odoo://model/nonexistent.model/schema` returns a JSON error object.
- [ ] `odoo://model/res.partner/records/99999999` returns a JSON error object.
- [ ] Unit tests with mocked client for all three resource patterns and error cases.

# Out of Scope

- Write operations via resources.
- Resource streaming or subscriptions.
