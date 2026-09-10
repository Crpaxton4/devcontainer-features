# Data Files

## XML Record Syntax

```xml
<?xml version="1.0" encoding="UTF-8"?>
<odoo>
    <!-- Basic record -->
    <record id="my_record" model="my.model">
        <field name="name">My Record</field>
        <field name="state">draft</field>
        <field name="active" eval="True"/>
        <field name="sequence" eval="10"/>
        <field name="partner_id" ref="base.res_partner_1"/>
        <field name="date" eval="fields.Date.today()"/>
        <field name="date_str">2025-01-01</field>
    </record>
</odoo>
```

## Field Value Methods

| Method | Example | Use |
|--------|---------|-----|
| plain text | `<field name="name">Foo</field>` | String/char fields |
| `ref="xml.id"` | `<field name="partner_id" ref="base.main_partner"/>` | Many2one by XML ID |
| `eval="expr"` | `<field name="amount" eval="100.0 * 1.21"/>` | Python expression |
| `eval="ref('xml.id')"` | Equivalent to `ref=` but inside an expression | M2o in eval context |

## eval Context Variables

```xml
<!-- Available in eval="" -->
eval="ref('base.main_company')"
eval="fields.Date.today()"
eval="fields.Datetime.now()"
eval="True" / eval="False"
eval="[(4, ref('base.group_user'))]"  <!-- M2M command -->
```

## Many2many Commands in eval

```xml
<field name="groups_id" eval="[
    (4, ref('base.group_user')),
    (4, ref('base.group_system')),
]"/>
<!-- (4, id) = link; (6, 0, [ids]) = replace all -->
<field name="tag_ids" eval="[(6, 0, [ref('tag_a'), ref('tag_b')])]"/>
```

## noupdate

Records inside `<data noupdate="1">` are only created on first install — never overwritten on upgrade.

```xml
<odoo>
    <!-- Overwritten on every upgrade (default noupdate=0) -->
    <record id="view_form" model="ir.ui.view">...</record>

    <data noupdate="1">
        <!-- Created once; upgrade ignores changes here -->
        <record id="default_config" model="my.model">
            <field name="name">Default Config</field>
        </record>
    </data>
</odoo>
```

## Deleting Records

```xml
<delete model="ir.rule" search="[('name', '=', 'Old Rule')]"/>
<delete model="ir.ui.view" id="old_addon.old_view_id"/>
```

## function Tag (Python calls in data)

```xml
<function model="res.partner" name="write" eval="[ref('base.main_partner')], {'active': True}"/>
```

## CSV Data Files

For `ir.model.access` and other bulk data. Simpler than XML for tabular data.

```csv
id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink
access_my_model,my.model access,model_my_model,base.group_user,1,0,0,0
```

Rules:
- First column must be `id`
- Relational columns: `field_name:id` to use XML IDs; `field_name` to use database IDs
- Boolean: `1`/`0`

## File Load Order

Data files in `__manifest__.py` `data` list are loaded **in order**. Dependencies must come first:

```python
'data': [
    'security/ir.model.access.csv',   # access before views
    'security/security.xml',
    'data/sequences.xml',
    'views/views.xml',                 # views before menus (menus ref actions)
    'views/menus.xml',
],
```

## External IDs (XML IDs)

```python
# Get record by XML ID in Python
record = self.env.ref('my_addon.my_record')

# Get XML ID of a record
xml_id = record.get_external_id()[record.id]  # e.g., 'my_addon.my_record'
```
