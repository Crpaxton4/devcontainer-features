# Security

## ir.model.access.csv

Controls CRUD access per model per group.

```csv
id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink
access_my_model,my_model,model_my_model,base.group_user,1,0,0,0
access_my_model_manager,my_model manager,model_my_model,my_addon.group_manager,1,1,1,1
```

| Column | Notes |
|--------|-------|
| `id` | Unique XML ID (module-scoped) |
| `name` | Human label (arbitrary) |
| `model_id:id` | `model_` + `_name` with dots → underscores |
| `group_id:id` | XML ID of `res.groups` record; empty = all users |
| `perm_read/write/create/unlink` | `1` = allowed, `0` = denied |

## Group Definition (XML)

```xml
<record id="group_my_user" model="res.groups">
    <field name="name">My Addon User</field>
    <field name="category_id" ref="base.module_category_hidden"/>
    <field name="implied_ids" eval="[(4, ref('base.group_user'))]"/>
</record>

<record id="group_my_manager" model="res.groups">
    <field name="name">My Addon Manager</field>
    <field name="category_id" ref="base.module_category_hidden"/>
    <field name="implied_ids" eval="[(4, ref('group_my_user'))]"/>
</record>
```

## Record Rules (XML)

Row-level access control. Filters which records a user can see/modify.

```xml
<record id="rule_my_model_own" model="ir.rule">
    <field name="name">My Model: Own Records</field>
    <field name="model_id" ref="model_my_model"/>
    <field name="groups" eval="[(4, ref('base.group_user'))]"/>
    <field name="domain_force">[('user_id', '=', user.id)]</field>
    <field name="perm_read" eval="True"/>
    <field name="perm_write" eval="True"/>
    <field name="perm_create" eval="True"/>
    <field name="perm_unlink" eval="False"/>
</record>

<!-- Global rule (no groups = applies to everyone including admins) -->
<record id="rule_my_model_company" model="ir.rule">
    <field name="name">My Model: Company</field>
    <field name="model_id" ref="model_my_model"/>
    <field name="domain_force">[('company_id', 'in', company_ids)]</field>
</record>
```

### domain_force Variables

| Variable | Value |
|----------|-------|
| `user` | `res.users` record |
| `user.id` | Current user ID |
| `company_id` | Active company ID |
| `company_ids` | All accessible company IDs |
| `time` | Python `time` module |

## Field-Level Access

```python
# Restrict field to group via field definition
secret_field = fields.Char(groups='my_addon.group_manager')
```

```xml
<!-- Or restrict in view -->
<field name="secret_field" groups="my_addon.group_manager"/>
```

## Checking Access in Code

```python
# Check if current user has group
self.env.user.has_group('my_addon.group_manager')

# Check model-level access
self.env['my.model'].check_access_rights('write')  # raises if denied

# Check record-level access
record.check_access_rule('write')  # raises if denied
```

## Common Patterns

```xml
<!-- Menu item visible only to managers -->
<menuitem id="menu_advanced" name="Advanced" groups="my_addon.group_manager"/>

<!-- View only for admins -->
<record id="view_form" model="ir.ui.view">
    <field name="groups_id" eval="[(4, ref('base.group_system'))]"/>
</record>
```
