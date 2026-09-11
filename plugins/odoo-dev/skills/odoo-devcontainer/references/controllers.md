# Controllers

## Basic Route

```python
from odoo import http
from odoo.http import request

class MyController(http.Controller):

    @http.route('/my/endpoint', auth='user', methods=['GET'], type='http')
    def my_page(self, **kwargs):
        records = request.env['my.model'].search([])
        return request.render('my_addon.my_template', {'records': records})

    @http.route('/my/api/data', auth='user', methods=['POST'], type='json')
    def my_json_api(self, record_id, **kwargs):
        record = request.env['my.model'].browse(record_id)
        return {'name': record.name, 'state': record.state}
```

## @http.route Parameters

| Parameter | Values | Notes |
|-----------|--------|-------|
| `route` | string or list | URL path(s); support `<int:id>`, `<string:name>`, `<path:subpath>` |
| `auth` | see below | Authentication method |
| `methods` | `['GET']`, `['POST']`, etc. | Default: all methods |
| `type` | `'http'` / `'json'` | `json` auto-parses body + returns JSON |
| `cors` | `'*'` or origin | CORS header value |
| `csrf` | `True` / `False` | CSRF check; default `True` for POST `http` routes |
| `website` | `True` | Enables website context (multi-website, theme) |
| `sitemap` | `True` / `False` / callable | Include in sitemap.xml |

## auth Values

| Value | Access |
|-------|--------|
| `'user'` | Must be logged-in internal user |
| `'public'` | Logged in OR anonymous (portal + public) |
| `'none'` | No auth — `request.env.user` is odoobot; no session |
| `'bearer'` | API key auth (v17+) |

## request Object

```python
request.env           # current Environment (with user from session)
request.env.user      # logged-in user (or public user)
request.httprequest   # werkzeug Request
request.params        # merged GET + POST params dict
request.session       # session dict
request.render(template, values)     # render QWeb → Response
request.redirect(url, code=303)      # redirect
request.make_response(data, headers) # raw response
request.not_found()                  # 404 response
```

## Route Converters

```python
@http.route('/product/<int:product_id>', auth='public', type='http')
def product_page(self, product_id, **kwargs):
    product = request.env['product.template'].browse(product_id)
    ...

@http.route('/blog/<string:slug>', auth='public', type='http')
def blog_post(self, slug, **kwargs):
    ...
```

## JSON Endpoint Pattern

```python
@http.route('/api/my_model/get', auth='user', type='json', methods=['POST'])
def get_record(self, record_id):
    record = request.env['my.model'].browse(record_id).exists()
    if not record:
        raise ValueError(f"Record {record_id} not found")
    return {
        'id': record.id,
        'name': record.name,
    }
```

- Request body must be `{"jsonrpc":"2.0","method":"call","params":{"record_id":1}}`
- Return value is automatically JSON-serialized in the `result` field
- Raise any exception → becomes `error` in response

## CSRF

- `type='json'` routes: CSRF **not** checked (JSON body is CSRF-safe by default)
- `type='http'` POST routes: CSRF token required in form or header `X-CSRF-Token`
- Disable for public APIs: `csrf=False` (only when protected by other means)

## Returning Files / Attachments

```python
@http.route('/my/download/<int:rec_id>', auth='user', type='http')
def download(self, rec_id, **kwargs):
    record = request.env['my.model'].browse(rec_id)
    data = record.generate_pdf()
    return request.make_response(
        data,
        headers=[
            ('Content-Type', 'application/pdf'),
            ('Content-Disposition', 'attachment; filename="report.pdf"'),
        ],
    )
```
