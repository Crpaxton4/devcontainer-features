# ORM Recordsets

## Search

```python
# search(domain, limit=None, offset=0, order=None, count=False)
partners = self.env['res.partner'].search([('is_company', '=', True)], limit=10)

# search_count(domain)
n = self.env['res.partner'].search_count([('country_id.code', '=', 'US')])

# search_read(domain, fields, limit, offset, order)
rows = self.env['res.partner'].search_read(
    [('active', '=', True)],
    ['name', 'email'],
    limit=50,
)
```

## Domain Syntax

```python
# Leaf: [field, operator, value]
# Operators: = != < > <= >= like ilike in not in child_of parent_of =like =ilike
[('name', 'ilike', 'acme')]
[('id', 'in', [1, 2, 3])]

# Logical: '&' (default), '|', '!'
['|', ('type', '=', 'out_invoice'), ('type', '=', 'out_refund')]
['&', ('active', '=', True), ('partner_id.country_id.code', '=', 'US')]
['!', ('state', '=', 'cancel')]

# Empty domain matches all records
[]
```

## Create / Write / Unlink

```python
# create — returns new recordset
record = self.env['my.model'].create({'name': 'New', 'value': 42})

# Batch create (preferred, v16+)
records = self.env['my.model'].create([
    {'name': 'A'}, {'name': 'B'},
])

# write — returns True
self.env['my.model'].search([('state', '=', 'draft')]).write({'active': False})

# unlink — returns True
record.unlink()
```

## Relational Field Assignment in vals

```python
# Many2one: pass ID or record
vals = {'partner_id': partner.id}

# One2many / Many2many command tuples
vals = {
    'line_ids': [
        (0, 0, {'name': 'New line', 'qty': 1}),  # create
        (1, line.id, {'qty': 2}),                  # update
        (2, line.id, 0),                           # delete
        (3, line.id, 0),                           # unlink (M2M)
        (4, line.id, 0),                           # link (M2M)
        (5, 0, 0),                                 # clear all (M2M)
        (6, 0, [id1, id2]),                        # replace set (M2M)
    ]
}
```

## Filtering & Traversal

```python
# filtered(func or field_name)
confirmed = records.filtered(lambda r: r.state == 'confirmed')
active = records.filtered('active')

# filtered_domain(domain) — v16+, in-memory domain filter
us_records = records.filtered_domain([('country_id.code', '=', 'US')])

# mapped(func or field_path)
names = records.mapped('name')             # list of values
partners = records.mapped('partner_id')    # recordset (deduped)
totals = records.mapped(lambda r: r.qty * r.price)  # list

# sorted(key, reverse=False)
by_name = records.sorted('name')
by_total = records.sorted(lambda r: r.total, reverse=True)
```

## Recordset Operators

```python
a | b    # union (deduped)
a & b    # intersection
a - b    # difference
a + b    # concatenation (keeps duplicates)
r in s   # membership
len(r)   # count
bool(r)  # False if empty
r[0]     # first record (raises if empty)
r[:5]    # slice
```

## Existence & Reads

```python
# exists() — filters out deleted records
records = records.exists()

# read(fields) — returns list of dicts (bypasses Python getters)
data = records.read(['name', 'state'])

# read_group(domain, fields, groupby, limit, offset, orderby, lazy)
groups = self.env['sale.order'].read_group(
    [('state', '=', 'sale')],
    ['amount_total:sum', 'partner_id'],
    ['partner_id'],
)

# ids property
print(records.ids)  # [1, 2, 3]
```

## browse

```python
# Construct recordset from known IDs without a DB query
record = self.env['res.partner'].browse(42)
records = self.env['res.partner'].browse([1, 2, 3])
```

## ensure_one

```python
def process(self):
    self.ensure_one()  # raises ValueError if len != 1
    return self.name
```
