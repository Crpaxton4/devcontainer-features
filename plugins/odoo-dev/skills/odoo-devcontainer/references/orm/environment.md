# ORM Environment

## Accessing env

```python
# Inside a model method
self.env           # current Environment
self.env.user      # res.users record of current user
self.env.uid       # integer UID
self.env.cr        # psycopg2 cursor (raw SQL)
self.env.context   # frozendict of context values
self.env.company   # res.company record (active company)
self.env.companies # all active companies (multi-company)
self.env.lang      # active language code string
```

## env.ref

```python
# Get record by XML ID
admin = self.env.ref('base.user_admin')
tax = self.env.ref('account.tax_template_sale_10')
```

## Switching User / Company / Context

```python
# sudo() — elevate to superuser (no user, bypasses access rights)
# v19: sudo() takes NO arguments
record_su = self.env['res.partner'].sudo().search([])

# with_user(user) — switch to a specific user
record_as = self.env['res.partner'].with_user(self.env.ref('base.user_demo'))

# with_company(company) — switch active company
env_co = self.env.with_company(self.env.ref('base.main_company'))

# with_context(**kwargs) — extend context
env_nolang = self.env.with_context(lang='en_US')
records_nolang = records.with_context(lang='en_US')

# Chaining
result = self.env['my.model'].with_user(user).with_context(no_check=True).search([])
```

> **v19 breaking change**: `env.sudo(user)` removed — use `env.with_user(user)` then `.sudo()` separately.

## Context Patterns

```python
# Read without language translation
self.with_context(lang=False).name

# Skip mail notification on write
record.with_context(mail_notrack=True).write({'state': 'done'})

# Force no recompute (use sparingly)
record.with_context(recompute=False).write({'x': 1})

# Pass data to onchange / downstream logic
record.with_context(default_partner_id=partner.id).create({})
```

## Raw SQL via cursor

```python
self.env.cr.execute(
    "SELECT id FROM res_partner WHERE active = %s",
    (True,),
)
rows = self.env.cr.fetchall()
```

- Use `%s` placeholders (never f-strings — SQL injection risk)
- Invalidate ORM cache after raw writes: `self.env['res.partner'].invalidate_model()`

## env['model'] vs self.env

```python
# Access any model
order = self.env['sale.order'].browse(order_id)

# Shorthand on self for same model
new = self.create({'name': 'X'})  # equiv to self.env['my.model'].create(...)
```

## Useful env Checks

```python
self.env.is_superuser()   # True when running as superuser (sudo)
self.env.is_admin()       # True for group_system members
self.env.is_user()        # True for any internal user
```
