# Feature Name

Domain Builder Ergonomics

# Goal

## Problem

`DomainExpression` can normalize and serialize domains, but composing complex domains requires manually constructing prefix-notation lists. There is no class-level `AND` / `OR` helper, no `TRUE` / `FALSE` constant, and no Python operator support. Developers building multi-condition searches must write raw list manipulation code that is error-prone and visually noisy.

## Solution

Add class-level composition methods (`AND`, `OR`), constants (`TRUE`, `FALSE`), Python operator support (`~`, `&`, `|`), and explicit documentation for dynamic time value strings to `DomainExpression`.

# Requirements

## Functional Requirements

- `DomainExpression.AND(iterable) -> DomainExpression` — combines an iterable of `DomainExpression` instances or domain lists with `&` operators; `AND([])` returns `DomainExpression.TRUE`; `AND([d])` returns `d` unchanged.
- `DomainExpression.OR(iterable) -> DomainExpression` — combines an iterable with `|` operators; `OR([])` returns `DomainExpression.FALSE`; `OR([d])` returns `d` unchanged.
- `DomainExpression.TRUE` — a class-level constant that serializes to `[]` (empty domain, matches all).
- `DomainExpression.FALSE` — a class-level constant that serializes to `[('id', '=', False)]` (matches nothing).
- `__invert__` (`~d`) — wraps the domain in a `!` prefix negation node; returns a new `DomainExpression`.
- `__and__` (`d1 & d2`) — equivalent to `DomainExpression.AND([d1, d2])`; returns a new `DomainExpression`.
- `__or__` (`d1 | d2`) — equivalent to `DomainExpression.OR([d1, d2])`; returns a new `DomainExpression`.
- Dynamic time value strings — when a domain condition's value is a string matching Odoo's dynamic time format (`'now'`, `'today'`, `'-3d +1H'`, `'=monday -1w'`, etc.), the value is passed through to the server unchanged; no client-side evaluation is performed.

## Non-Functional Requirements

- All composition operations are immutable: they return new `DomainExpression` instances and do not modify operands.
- `TRUE` and `FALSE` are class-level constants, not instance constructors.
- Operators accept both `DomainExpression` instances and raw domain lists as operands, normalizing them as needed.

# Acceptance Criteria

- [ ] `DomainExpression.AND([d1, d2])` serializes to `['&', ...d1, ...d2]`.
- [ ] `DomainExpression.OR([d1, d2])` serializes to `['|', ...d1, ...d2]`.
- [ ] `DomainExpression.AND([])` serializes to `[]` (TRUE).
- [ ] `DomainExpression.OR([])` serializes to `[('id', '=', False)]` (FALSE).
- [ ] `DomainExpression.AND([d])` serializes identically to `d`.
- [ ] `~d` wraps the domain with a `!` node.
- [ ] `d1 & d2` produces the same result as `DomainExpression.AND([d1, d2])`.
- [ ] `d1 | d2` produces the same result as `DomainExpression.OR([d1, d2])`.
- [ ] A condition with value `'-3d +1H'` serializes with the string value intact.
- [ ] Operators accept raw lists as operands without requiring explicit wrapping.
- [ ] Unit tests for each method and operator, including edge cases for empty and single-element inputs.

# Out of Scope

- Client-side evaluation of dynamic time values.
- Server-side domain optimization.
- Support for the `any` / `not any` operators (server-side feature, pass-through only).
