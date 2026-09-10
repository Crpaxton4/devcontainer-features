# Actions

## ir.actions.act_window

Opens a model's views in the main content area.

```xml
<record id="action_my_model" model="ir.actions.act_window">
    <field name="name">My Models</field>
    <field name="res_model">my.model</field>
    <field name="view_mode">list,form</field>
    <field name="domain">[('active', '=', True)]</field>
    <field name="context">{'default_state': 'draft'}</field>
    <field name="limit">80</field>
</record>
```

### Key Fields

| Field | Notes |
|-------|-------|
| `name` | Window/breadcrumb title |
| `res_model` | Target model |
| `view_mode` | Comma-separated view types; order matters |
| `domain` | Pre-filter records |
| `context` | Passed to views; use `default_*` to preset form fields |
| `res_id` | Open specific record directly (single form) |
| `views` | `[(view_id, type), ...]` to pin specific view records |
| `target` | `'current'` (default) / `'new'` (dialog) / `'inline'` / `'fullscreen'` |
| `limit` | Default page size (default: 80) |
| `search_view_id` | Pin specific search view |
| `filter` | Default search filter name |
| `group_by` | Default groupby |

## Menu Binding

```xml
<!-- Top-level menu -->
<menuitem id="menu_my_addon" name="My Addon" sequence="50"/>

<!-- Sub-menu -->
<menuitem id="menu_my_model"
    name="My Models"
    parent="menu_my_addon"
    action="action_my_model"
    sequence="10"/>
```

## Button Binding

```xml
<!-- Call Python method on record -->
<button name="action_confirm" type="object" string="Confirm"/>

<!-- Open action -->
<button name="%(action_my_model)d" type="action" string="See Related"/>

<!-- URL -->
<button name="https://odoo.com" type="url" string="Docs" icon="fa-external-link"/>
```

## ir.actions.server

Runs server-side logic. Can be triggered from buttons, menus, or automated actions.

```xml
<record id="action_server_set_done" model="ir.actions.server">
    <field name="name">Set Done</field>
    <field name="model_id" ref="model_my_model"/>
    <field name="binding_model_id" ref="model_my_model"/>  <!-- shows in Action menu -->
    <field name="state">code</field>
    <field name="code">
records.write({'state': 'done'})
    </field>
</record>
```

### state values

| `state` | Behavior |
|---------|---------|
| `code` | Execute Python code (vars: `env`, `model`, `records`, `record`, `action`) |
| `object_write` | Write fields on records |
| `object_create` | Create records |
| `multi` | Execute multiple child actions |
| `act_window` | Open a window action |
| `return_action` | Return an action dict |

## ir.actions.client

Opens an OWL component registered in the `"actions"` registry.

```xml
<record id="action_my_dashboard" model="ir.actions.client">
    <field name="name">My Dashboard</field>
    <field name="tag">my_addon.MyDashboard</field>
    <field name="context">{'default_period': 'month'}</field>
</record>
```

## ir.actions.act_url

Navigate to a URL.

```xml
<record id="action_docs" model="ir.actions.act_url">
    <field name="name">Documentation</field>
    <field name="url">https://docs.example.com</field>
    <field name="target">new</field>  <!-- new tab -->
</record>
```

## Automated Actions (ir.base_automation)

Trigger server actions on model events.

```xml
<record id="automation_on_confirm" model="ir.base_automation">
    <field name="name">On Confirm</field>
    <field name="model_id" ref="model_my_model"/>
    <field name="trigger">on_write</field>  <!-- on_create / on_write / on_create_or_write / on_unlink / on_change / based_on_timed_condition -->
    <field name="filter_pre_domain">[('state', '!=', 'confirmed')]</field>
    <field name="filter_domain">[('state', '=', 'confirmed')]</field>
    <field name="action_server_id" ref="action_server_set_done"/>
</record>
```
