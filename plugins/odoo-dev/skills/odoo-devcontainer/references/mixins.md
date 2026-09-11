# Mixins

## mail.thread (Chatter)

Adds message thread, followers, and log notes to a model.

```python
class MyModel(models.Model):
    _name = 'my.model'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'My Model'

    name = fields.Char(tracking=True)         # track changes in chatter
    state = fields.Selection(..., tracking=10) # tracking priority (lower = shown first)
```

### Views — add chatter div

```xml
<form>
    <sheet>...</sheet>
    <div class="oe_chatter">
        <field name="message_follower_ids"/>
        <field name="activity_ids"/>
        <field name="message_ids"/>
    </div>
</form>
```

### Sending Messages in Code

```python
record.message_post(
    body="The order has been confirmed.",
    subtype_xmlid='mail.mt_note',     # mt_note (log) or mt_comment (message to followers)
    message_type='comment',
)

# Post with partner notifications
record.message_post(
    body="Please review this.",
    partner_ids=[partner.id],
    subtype_xmlid='mail.mt_comment',
)
```

## mail.activity.mixin

Adds activities (to-dos, calls, emails) to a model. Always paired with `mail.thread`.

```python
# Schedule an activity
record.activity_schedule(
    'mail.mail_activity_data_todo',     # activity type XML ID
    date_deadline=fields.Date.today() + timedelta(days=3),
    summary='Follow up',
    user_id=self.env.user.id,
)

# Mark done
record.activity_feedback(['mail.mail_activity_data_todo'], feedback='Done')
```

## mail.alias.mixin

Adds an email alias to a model (e.g., project@company.odoo.com creates project tasks).

```python
class Project(models.Model):
    _name = 'project.project'
    _inherit = ['mail.alias.mixin', 'mail.thread']

    def _alias_get_creation_values(self):
        return {
            'alias_model_id': self.env['ir.model']._get('project.task').id,
            'alias_defaults': {'project_id': self.id},
        }
```

## rating.mixin

Adds customer satisfaction rating to records.

```python
class MyModel(models.Model):
    _name = 'my.model'
    _inherit = ['rating.mixin', 'mail.thread']
```

- Adds `rating_ids` One2many and `rating_last_value` Float fields
- Use `record.rating_send_request()` to email a rating request

## website.published.mixin

For website-visible models — adds published/unpublished toggle.

```python
class BlogPost(models.Model):
    _name = 'blog.post'
    _inherit = ['website.published.mixin']

    # Adds:
    # website_published: Boolean
    # website_url: Char (computed — must override _default_website_url)

    def _default_website_url(self):
        return f'/blog/{self.id}'
```

## website.seo.metadata

Adds SEO fields (meta title, description, keywords).

```python
_inherit = ['website.seo.metadata']
# Adds: website_meta_title, website_meta_description, website_meta_keywords
```

## Combining Mixins

```python
class MyModel(models.Model):
    _name = 'my.model'
    _inherit = [
        'mail.thread',
        'mail.activity.mixin',
        'portal.mixin',        # portal access (share with external users)
    ]
    _description = 'My Model'
```

## portal.mixin

Makes records accessible to portal users.

```python
_inherit = ['portal.mixin']

# Must implement:
def _compute_access_url(self):
    for rec in self:
        rec.access_url = f'/my/model/{rec.id}'
```
