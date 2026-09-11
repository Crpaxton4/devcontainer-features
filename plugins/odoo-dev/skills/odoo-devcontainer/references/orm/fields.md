# ORM Fields

## Basic Field Types

| Type | Args | Notes |
|------|------|-------|
| `fields.Char` | `size=None` | Unicode string |
| `fields.Text` | | Multiline string |
| `fields.Html` | `sanitize=True` | Sanitized HTML |
| `fields.Integer` | | Python int |
| `fields.Float` | `digits=(16,2)` | `digits` = (precision, scale) or ref to `decimal.precision` |
| `fields.Monetary` | `currency_field='currency_id'` | Always pair with Many2one to `res.currency` |
| `fields.Boolean` | | |
| `fields.Date` | | `date` object; use `fields.Date.today()` |
| `fields.Datetime` | | `datetime` object (UTC); use `fields.Datetime.now()` |
| `fields.Binary` | `attachment=True` | Store as `ir.attachment` when `attachment=True` |
| `fields.Image` | `max_width=1920, max_height=1920` | Subclass of Binary with resize |
| `fields.Selection` | `selection=[...]` or method name | |
| `fields.Reference` | `selection=[...]` | Dynamic Many2one across models |

## Relational Field Types

| Type | Required Args | Notes |
|------|---------------|-------|
| `fields.Many2one` | `comodel_name` | FK; `ondelete='set null'\|'restrict'\|'cascade'` |
| `fields.One2many` | `comodel_name, inverse_name` | Virtual; inverse of Many2one |
| `fields.Many2many` | `comodel_name` | Auto junction table; override with `relation, column1, column2` |

## Common Field Kwargs

| Kwarg | Default | Notes |
|-------|---------|-------|
| `string` | field name | UI label |
| `required` | `False` | |
| `readonly` | `False` | |
| `index` | `False` | `True` / `'btree'` / `'btree_not_null'` / `'trigram'` |
| `default` | `None` | value, callable, or lambda |
| `help` | `''` | Tooltip |
| `copy` | field-dependent | Whether copied on `copy()` |
| `groups` | `''` | CSV of group XML IDs restricting access |
| `store` | `True` | Set `False` on computed to avoid DB column |
| `compute` | | Method name string or callable |
| `inverse` | | Method to write back computed value |
| `search` | | Method to support `search()` on computed field |
| `related` | | Dot-path string e.g. `'partner_id.country_id.name'` |
| `depends_context` | | Tuple of context keys that invalidate cache |
| `precompute` | `False` | Compute before record is saved (v16+) |

## Computed Fields

```python
name = fields.Char(compute='_compute_name', store=True)

@api.depends('first_name', 'last_name')
def _compute_name(self):
    for rec in self:
        rec.name = f"{rec.first_name} {rec.last_name}"
```

- `store=True` → written to DB; triggers must be listed in `@api.depends`
- `store=False` (default) → recomputed on every read; `@api.depends` still required for cache invalidation

## Related Fields

```python
country_name = fields.Char(related='partner_id.country_id.name', store=True)
```

- Automatically readonly unless `readonly=False` explicitly set
- `store=True` copies value to column; automatically updates via triggers

## Selection Field

```python
state = fields.Selection([
    ('draft', 'Draft'),
    ('confirmed', 'Confirmed'),
    ('done', 'Done'),
], default='draft')

# Dynamic selection via method
category = fields.Selection(selection='_get_categories')

def _get_categories(self):
    return [('a', 'A'), ('b', 'B')]
```

## v19 Notes

- `filtered_domain()` works on recordsets for in-memory filtering
- `index='btree_not_null'` skips indexing NULL values (saves space for sparse fields)
- `precompute=True` computes during `create()` before flush, useful for required computed fields
