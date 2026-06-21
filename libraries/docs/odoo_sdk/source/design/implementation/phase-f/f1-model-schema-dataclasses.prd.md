# Feature Name

ModelSchema and FieldSchema Dataclasses

# Goal

## Problem

Schema data returned by `ir.model` and `ir.model.fields` is currently accessed as raw dicts. There is no typed representation that Phase G (Pydantic generation) and Phase H (MCP resources) can build on.

## Solution

Define `ModelSchema` and `FieldSchema` as immutable frozen dataclasses in a new `src/odoo_sdk/reflection/schema.py` module.

# Requirements

## Functional Requirements

- `FieldSchema` is a frozen dataclass with: `name: str`, `ttype: str`, `string: str`, `required: bool = False`, `readonly: bool = False`, `store: bool = True`, `compute: str | None = None`, `relation: str | None = None`, `selection: list[tuple[str, str]] | None = None`, `domain: str | None = None`, `help: str = ''`.
- `FieldSchema.is_relational -> bool` — `True` when `ttype` in `{'many2one', 'one2many', 'many2many'}`.
- `FieldSchema.is_computed -> bool` — `True` when `compute` is not `None` and not empty.
- `FieldSchema.is_stored -> bool` — `True` when `store` is `True`.
- `ModelSchema` is a frozen dataclass with: `name: str`, `description: str`, `state: str`, `fields: dict[str, FieldSchema]`.
- `ModelSchema.get_field(name) -> FieldSchema | None` — returns the named field or `None`.
- `ModelSchema.required_fields -> list[FieldSchema]` — returns all fields where `required=True` and `store=True` and not `compute`.
- Both dataclasses handle missing `ir.model.fields` attributes gracefully using field defaults (Odoo versions may not return every attribute).

## Non-Functional Requirements

- Both dataclasses must be immutable (frozen).
- Construction must not require a live Odoo connection (pure data objects).

# Acceptance Criteria

- [ ] `FieldSchema` instantiates with only `name` and `ttype` provided; all others default.
- [ ] `FieldSchema.is_relational` returns `True` for `many2one`, `False` for `char`.
- [ ] `FieldSchema.is_computed` returns `True` when `compute` is set.
- [ ] `ModelSchema.required_fields` returns only stored, non-computed required fields.
- [ ] `ModelSchema.get_field('nonexistent')` returns `None`.
- [ ] Unit tests cover all properties and edge cases.

# Out of Scope

- Pydantic model generation from `FieldSchema`.
- Write-side field validation.
