# ORM Decorators

## @api.depends

Marks a compute method and declares its field dependencies. Re-runs when any listed field changes.

```python
@api.depends('line_ids.price_unit', 'line_ids.qty')
def _compute_total(self):
    for rec in self:
        rec.total = sum(l.price_unit * l.qty for l in rec.line_ids)
```

- Dot-path traversal: `'partner_id.country_id'` — tracks both fields
- Use `'line_ids'` to depend on the entire relational set (add/remove lines)
- `store=True` fields recompute on write; `store=False` recompute on read (cache miss)

## @api.depends_context

Invalidates cache when listed context keys change. Use for user/company-aware computed fields.

```python
@api.depends_context('uid', 'company')
def _compute_allowed(self):
    ...
```

## @api.onchange

Triggers in the UI when listed fields change. Operates on an unsaved virtual record.

```python
@api.onchange('partner_id')
def _onchange_partner(self):
    if self.partner_id:
        self.pricelist_id = self.partner_id.property_product_pricelist
    # Return warning dict (optional)
    return {'warning': {'title': 'Warning', 'message': 'Check dates'}}
```

- Only fires in the web client, not on programmatic writes
- `self` is a single record (no loop needed)
- Can set other fields; can return `{'warning': {...}}` or `{'domain': {...}}`

## @api.constrains

Raises `ValidationError` to block saves. Runs on create and write when listed fields change.

```python
from odoo.exceptions import ValidationError

@api.constrains('start_date', 'end_date')
def _check_dates(self):
    for rec in self:
        if rec.start_date > rec.end_date:
            raise ValidationError("Start must be before end.")
```

- Always iterate `self` — method receives a recordset
- Does NOT run when listed fields are not being written
- Use `sql_constraints` in `_sql_constraints` for DB-level enforcement (faster)

## @api.model

Declares that the method does not operate on a specific record. `self` is the model, not a recordset.

```python
@api.model
def create(self, vals):
    vals['code'] = self._generate_code()
    return super().create(vals)
```

- Use for `create()` overrides and class-level helpers
- v19+: prefer `@api.model_create_multi` for `create()`

## @api.model_create_multi

v16+ preferred override for `create()`. Receives a list of value dicts; must return recordset.

```python
@api.model_create_multi
def create(self, vals_list):
    for vals in vals_list:
        if not vals.get('ref'):
            vals['ref'] = self.env['ir.sequence'].next_by_code('my.model')
    return super().create(vals_list)
```

- Handles both single and batch creates efficiently
- Replaces the old `@api.model def create(self, vals)` pattern

## @api.returns

Declares the return type for methods that return recordsets. Used internally for RPC serialization.

```python
@api.returns('res.partner', lambda value: value.id)
def get_partner(self):
    return self.partner_id
```

## Override Patterns

```python
# Write override
def write(self, vals):
    result = super().write(vals)
    if 'state' in vals:
        self._notify_state_change()
    return result

# Unlink override
def unlink(self):
    for rec in self:
        if rec.state == 'done':
            raise UserError("Cannot delete done records.")
    return super().unlink()
```
