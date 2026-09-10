# ORM Inheritance

Odoo has three distinct inheritance mechanisms. Pick the right one.

## 1. Extension — `_inherit` only

Adds/modifies fields and methods on the **same model** (same DB table).

```python
class ResPartner(models.Model):
    _inherit = 'res.partner'

    vat_verified = fields.Boolean(string='VAT Verified')

    def action_verify_vat(self):
        ...
```

- Same `_name` (implicitly inherited)
- Same DB table — new fields become new columns
- Use for all addons that extend standard models

## 2. Copy — `_name` + `_inherit`

Creates a **new model** with a **new DB table**, copying all fields/methods from the parent.

```python
class ProjectTaskCopy(models.Model):
    _name = 'project.task.copy'
    _inherit = 'project.task'
    _description = 'Task Copy'

    extra_field = fields.Char()
```

- New `_name` required
- Separate DB table; no relation to original records
- Rarely needed — most use cases are better served by extension

## 3. Delegation — `_inherits`

Embeds a parent record via a Many2one. Child model **delegates** field access to parent. Parent has its own DB table.

```python
class HrEmployee(models.Model):
    _name = 'hr.employee'
    _inherits = {'res.partner': 'address_id'}

    address_id = fields.Many2one('res.partner', required=True, ondelete='restrict')
    department_id = fields.Many2one('hr.department')
```

- `address_id.name` is accessible as `employee.name` directly
- Reading/writing delegated fields transparently reads/writes the partner record
- The Many2one column **must** exist in `_inherits` dict value

## Comparison Table

| | Extension | Copy | Delegation |
|---|-----------|------|-----------|
| `_name` | inherited | new | new |
| `_inherit` | `'parent'` | `'parent'` | — |
| `_inherits` | — | — | `{'parent': 'fk_field'}` |
| DB table | same as parent | new table | new table + parent table |
| Use case | extend existing model | brand-new model from template | embed/compose models |

## Method Override Pattern

Always call `super()` unless intentionally blocking parent behavior.

```python
class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        result = super().action_confirm()
        self._send_custom_notification()
        return result
```

## _inherit as List

Use a list to inherit from multiple classes (mixins):

```python
class MyModel(models.Model):
    _name = 'my.model'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'My Model'
```
