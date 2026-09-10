# Reports

## ir.actions.report

```xml
<record id="action_report_my_model" model="ir.actions.report">
    <field name="name">My Model Report</field>
    <field name="model">my.model</field>
    <field name="report_type">qweb-pdf</field>        <!-- qweb-pdf / qweb-html / xlsx -->
    <field name="report_name">my_addon.report_my_model_doc</field>  <!-- QWeb template name -->
    <field name="report_file">my_addon.report_my_model_doc</field>  <!-- PDF filename base -->
    <field name="print_report_name">'My Report - %s' % object.name</field>  <!-- per-record name -->
    <field name="binding_model_id" ref="model_my_model"/>  <!-- adds to Print menu -->
    <field name="binding_type">report</field>
    <field name="paperformat_id" ref="base.paperformat_euro"/>
</record>
```

### report_type Values

| Value | Output |
|-------|--------|
| `qweb-pdf` | PDF via wkhtmltopdf |
| `qweb-html` | HTML preview |
| `xlsx` | Excel (requires `report_xlsx` module) |

## QWeb Report Template

```xml
<template id="report_my_model_doc">
    <t t-call="web.html_container">
        <t t-foreach="docs" t-as="doc">
            <t t-call="web.external_layout">
                <div class="page">
                    <h2 t-field="doc.name"/>
                    <p>Date: <span t-field="doc.date"/></p>
                    <table class="table">
                        <thead>
                            <tr>
                                <th>Product</th>
                                <th>Qty</th>
                                <th>Price</th>
                            </tr>
                        </thead>
                        <tbody>
                            <t t-foreach="doc.line_ids" t-as="line">
                                <tr>
                                    <td t-field="line.product_id.name"/>
                                    <td t-field="line.qty"/>
                                    <td t-field="line.price_unit"
                                        t-options='{"widget": "monetary", "display_currency": doc.currency_id}'/>
                                </tr>
                            </t>
                        </tbody>
                    </table>
                </div>
            </t>
        </t>
    </t>
</template>
```

### Template Variables

| Variable | Value |
|----------|-------|
| `docs` | Recordset of records being printed |
| `doc` | Current record in `t-foreach` |
| `doc_ids` | List of IDs |
| `doc_model` | Model name string |
| `user` | `res.users` current user |
| `company` | `res.company` active company |
| `time` | Python `time` module |
| `datetime` | Python `datetime` module |

## Paper Format

```xml
<record id="paperformat_my_label" model="report.paperformat">
    <field name="name">My Label</field>
    <field name="page_height">50</field>    <!-- mm -->
    <field name="page_width">100</field>   <!-- mm -->
    <field name="format">custom</field>
    <field name="orientation">Portrait</field>
    <field name="margin_top">5</field>
    <field name="margin_bottom">5</field>
    <field name="margin_left">5</field>
    <field name="margin_right">5</field>
    <field name="header_line" eval="False"/>
    <field name="header_spacing">3</field>
</record>
```

## Programmatic Render

```python
# Render to bytes
pdf_bytes, mime = self.env['ir.actions.report']._render_qweb_pdf(
    'my_addon.report_my_model_doc',
    res_ids=[record.id],
)

# As attachment
self.env['ir.attachment'].create({
    'name': f'{record.name}.pdf',
    'res_model': record._name,
    'res_id': record.id,
    'datas': base64.b64encode(pdf_bytes),
    'mimetype': 'application/pdf',
})

# Return as download action
return self.env.ref('my_addon.action_report_my_model').report_action(records)
```

## Custom Report Model (data preprocessing)

```python
class ReportMyModel(models.AbstractModel):
    _name = 'report.my_addon.report_my_model_doc'
    _description = 'My Model Report'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['my.model'].browse(docids)
        return {
            'doc_ids': docids,
            'doc_model': 'my.model',
            'docs': docs,
            'custom_data': self._compute_custom_data(docs),
        }
```
