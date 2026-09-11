# Performance

## Prefetch & N+1

Odoo prefetches fields of the same model in batches. N+1 happens when you access related records one-by-one in a loop.

```python
# BAD — N+1: one query per record for partner name
for order in orders:
    print(order.partner_id.name)  # SELECT * FROM res_partner WHERE id = ?

# GOOD — prefetch partner fields before loop
orders.mapped('partner_id')  # triggers one batch SELECT
for order in orders:
    print(order.partner_id.name)  # hits cache

# ALSO GOOD — read() fetches in one query
data = orders.read(['name', 'partner_id'])
```

## read_group

Use instead of search+loop for aggregated data. Single SQL query with GROUP BY.

```python
# BAD
total = sum(order.amount_total for order in self.env['sale.order'].search([('state','=','sale')]))

# GOOD
result = self.env['sale.order'].read_group(
    domain=[('state', '=', 'sale')],
    fields=['amount_total:sum', 'partner_id'],
    groupby=['partner_id'],
)
# result: [{'partner_id': (id, name), 'amount_total': 1000.0, 'partner_id_count': 5}, ...]
```

## sql_constraints

Enforce uniqueness and checks at the DB level (faster than `@api.constrains`).

```python
class MyModel(models.Model):
    _name = 'my.model'

    _sql_constraints = [
        ('name_uniq', 'unique(name)', 'Name must be unique.'),
        ('code_company_uniq', 'unique(code, company_id)', 'Code must be unique per company.'),
        ('positive_qty', 'CHECK(qty >= 0)', 'Quantity cannot be negative.'),
    ]
```

## sudo() Cost

`sudo()` bypasses Python-level access checks but does NOT skip:
- `_sql_constraints` (DB enforced)
- `@api.constrains` (Python enforced)
- `write()` / `create()` overrides

It does skip:
- `ir.model.access` CRUD checks
- `ir.rule` domain filters

Avoid wrapping large operations in `sudo()` — it hides permission bugs.

## Indexes

```python
# Single-field index
name = fields.Char(index=True)

# Conditional index (skip NULLs — saves space for sparse fields)
ref = fields.Char(index='btree_not_null')

# Trigram index (for ilike searches)
name = fields.Char(index='trigram')
```

## Profiling

```python
# Enable profiler via context manager
from odoo.tools.profiler import profile

with profile(db=self.env.cr.dbname, description='My operation'):
    records = self.env['my.model'].search([('state', '=', 'draft')])
    records.write({'state': 'confirmed'})
```

Access profiling results at `/web#action=base_setup.action_general_configuration` → Developer Tools → Profiling, or via the debug menu.

## Batch Creates

```python
# BAD — N inserts
for name in names:
    self.env['my.model'].create({'name': name})

# GOOD — one insert (or few batched inserts)
self.env['my.model'].create([{'name': n} for n in names])
```

## with_prefetch / prefetch_ids

```python
# Force records to share prefetch buffer even if fetched separately
records = recordset_a.with_prefetch(recordset_b._ids)
```

## Raw SQL (Use Sparingly)

```python
# For reports/aggregations that ORM can't express efficiently
self.env.cr.execute("""
    SELECT partner_id, SUM(amount_total)
    FROM sale_order
    WHERE state = %s
    GROUP BY partner_id
""", ('sale',))
rows = self.env.cr.fetchall()

# Invalidate ORM cache after raw writes
self.env['sale.order'].invalidate_model(['amount_total'])
```

## Context Flags That Affect Performance

```python
# Skip computed field recomputation (use with care)
record.with_context(recompute=False).write({'x': 1})
self.env.add_to_compute(field, records)  # schedule for later

# Disable chatter tracking during bulk ops
records.with_context(mail_notrack=True).write({'state': 'done'})
records.with_context(tracking_disable=True).write({'state': 'done'})
```
