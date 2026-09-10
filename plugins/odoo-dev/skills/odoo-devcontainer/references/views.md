# Views

## View Types

| Type | `type` value | Primary use |
|------|-------------|-------------|
| Form | `form` | Single record edit/view |
| List | `list` (was `tree`) | Multi-record table |
| Kanban | `kanban` | Card-based board |
| Search | `search` | Search panel (filters/groupby) |
| Graph | `graph` | Bar/line/pie aggregation |
| Pivot | `pivot` | Spreadsheet-style aggregate |
| Calendar | `calendar` | Date-based events |
| Activity | `activity` | Activity scheduling view |
| Map | `map` | Geo pins (enterprise) |
| Gantt | `gantt` | Timeline bars (enterprise) |
| Cohort | `cohort` | Retention analysis (enterprise) |
| Dashboard | `dashboard` | Aggregate tiles (enterprise) |

## Form View

```xml
<record id="view_my_model_form" model="ir.ui.view">
    <field name="name">my.model.form</field>
    <field name="model">my.model</field>
    <field name="arch" type="xml">
        <form string="My Model">
            <header>
                <button name="action_confirm" type="object" string="Confirm"
                        class="btn-primary" invisible="state != 'draft'"/>
                <field name="state" widget="statusbar" statusbar_visible="draft,confirmed,done"/>
            </header>
            <sheet>
                <group>
                    <field name="name"/>
                    <field name="partner_id"/>
                </group>
                <notebook>
                    <page string="Lines">
                        <field name="line_ids">
                            <list editable="bottom">
                                <field name="product_id"/>
                                <field name="qty"/>
                                <field name="price_unit"/>
                            </list>
                        </field>
                    </page>
                    <page string="Notes">
                        <field name="note" widget="html"/>
                    </page>
                </notebook>
            </sheet>
            <div class="oe_chatter">
                <field name="message_follower_ids"/>
                <field name="activity_ids"/>
                <field name="message_ids"/>
            </div>
        </form>
    </field>
</record>
```

## List View

```xml
<record id="view_my_model_list" model="ir.ui.view">
    <field name="name">my.model.list</field>
    <field name="model">my.model</field>
    <field name="arch" type="xml">
        <list string="My Models" default_order="name" decoration-danger="state == 'cancel'">
            <field name="name"/>
            <field name="partner_id"/>
            <field name="state"/>
            <field name="total" sum="Total"/>
        </list>
    </field>
</record>
```

## Kanban View

```xml
<record id="view_my_model_kanban" model="ir.ui.view">
    <field name="name">my.model.kanban</field>
    <field name="model">my.model</field>
    <field name="arch" type="xml">
        <kanban default_group_by="stage_id">
            <field name="name"/>
            <field name="partner_id"/>
            <field name="state"/>
            <templates>
                <t t-name="card">
                    <field name="name" class="fw-bold"/>
                    <field name="partner_id"/>
                </t>
            </templates>
        </kanban>
    </field>
</record>
```

## Search View

```xml
<record id="view_my_model_search" model="ir.ui.view">
    <field name="name">my.model.search</field>
    <field name="model">my.model</field>
    <field name="arch" type="xml">
        <search>
            <field name="name" filter_domain="[('name', 'ilike', self)]"/>
            <field name="partner_id"/>
            <filter name="my_records" string="My Records" domain="[('user_id', '=', uid)]"/>
            <filter name="active" string="Active" domain="[('state', '!=', 'cancel')]"/>
            <separator/>
            <filter name="group_partner" string="Partner" context="{'group_by': 'partner_id'}"/>
        </search>
    </field>
</record>
```

## View Inheritance (XPath)

```xml
<record id="view_my_model_form_inherit" model="ir.ui.view">
    <field name="name">my.model.form.inherit</field>
    <field name="model">my.model</field>
    <field name="inherit_id" ref="my_addon.view_my_model_form"/>
    <field name="arch" type="xml">
        <!-- XPath positions: after, before, replace, inside, attributes -->
        <xpath expr="//field[@name='partner_id']" position="after">
            <field name="new_field"/>
        </xpath>

        <!-- Shorthand: match by field name (preferred for fields) -->
        <field name="name" position="after">
            <field name="code"/>
        </field>

        <!-- Modify attributes -->
        <field name="qty" position="attributes">
            <attribute name="invisible">state == 'done'</attribute>
            <attribute name="readonly">state != 'draft'</attribute>
        </field>

        <!-- Replace entire node -->
        <xpath expr="//button[@name='action_confirm']" position="replace">
            <button name="action_confirm_custom" type="object" string="Custom Confirm"/>
        </xpath>
    </field>
</record>
```

## Conditional Visibility / Readonly (v19)

```xml
<!-- v19 preferred: direct expressions (not attrs={}) -->
<field name="discount" invisible="pricelist_id.discount_policy != 'without_discount'"/>
<field name="note" readonly="state in ('done', 'cancel')"/>
<button name="action_cancel" required="state == 'draft'"/>

<!-- Legacy attrs (still supported but deprecated) -->
<field name="discount" attrs="{'invisible': [('discount_policy', '!=', 'without_discount')]}"/>
```

## ir.ui.view Fields

| Field | Notes |
|-------|-------|
| `name` | Unique identifier (convention: `model.view_type`) |
| `model` | Model name |
| `type` | View type; auto-detected from arch root tag |
| `arch` | XML definition |
| `inherit_id` | Parent view XML ID for inheritance |
| `mode` | `primary` (default) or `extension` |
| `priority` | Lower = applied first; default 16 |
| `groups_id` | Restrict view to specific groups |
| `active` | Disable with `eval="False"` |
