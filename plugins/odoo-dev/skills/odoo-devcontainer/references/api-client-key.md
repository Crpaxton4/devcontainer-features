# API Client — Token / Key Auth

Odoo calls OUT to a 3rd-party API authenticated by a static token or API key. Simplest outbound case: credential is fixed, no exchange, no refresh, no expiry.

Outline only. Structural skeleton, not a mandate. Adapt to the service.

## Where code lives — two observed shapes

**Shape A — method on the config model.** REST-ish, credential read off `self`. Base example: `payment.provider._stripe_make_request`.

**Shape B — standalone helper class** (plain class, not a Model), built with creds + a logger callback. Heavy protocol build reused across records. Base example: `delivery_easypost` `EasypostRequest`.

Odoo convention often adds a third tier: a **stateless `AbstractModel` service** owning raw transport (mirrors `google.service`), called by name `self.env['x.api']`. Prefer this when many records/models hit the same API — transport lives in one place, not copied into every business model's MRO. Do NOT put raw transport in a mixin that business records inherit.

## Credential storage

- Per-company key → `Char` on `res.company`, `groups="base.group_system"`. Matches multi-company paradigm.
- Global/single key → `ir.config_parameter` (read with `.sudo()`).
- Never: source file, attachment, plain non-grouped field.

```python
class ResCompany(models.Model):
    _inherit = "res.company"
    x_api_key = fields.Char(string="X API Key", groups="base.group_system")
```

## Transport skeleton

```python
class XApi(models.AbstractModel):
    _name = "x.api"
    _description = "X API Client"

    @api.model
    def _timeout(self):
        return int(self.env["ir.config_parameter"].sudo()
                   .get_param("x.request_timeout", DEFAULT_TIMEOUT))

    @api.model
    def _headers(self):
        key = self.env.company.sudo().x_api_key
        if not key:
            raise UserError(_("No API key configured for %s.", self.env.company.display_name))
        return {"Authorization": f"Bearer {key}", "Accept": ACCEPT}

    @api.model
    def _http(self, method, endpoint, **kwargs):
        url = url_join(API_BASE, endpoint)
        try:
            resp = requests.request(method, url, headers=self._headers(),
                                    timeout=self._timeout(), **kwargs)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError:
            _logger.exception("unreachable: %s", url)
            raise UserError(_("Could not reach the X API."))
        except requests.exceptions.HTTPError:
            _logger.exception("bad request: %s", url)
            raise UserError(_("The X API rejected the request."))
        return resp.json()
```

## Common structural elements

- HTTP lib = `requests`. Always. No urllib/httpx.
- `timeout=` on EVERY call. Non-negotiable. Source it from `ir.config_parameter` so it is tunable.
- Base URL = module constant, joined with `werkzeug.urls.url_join`.
- Auth header injected in one place (`_headers`), not per call site.
- Return parsed `.json()`, not the raw `Response`.
- Errors → Odoo exception. Catch `ConnectionError` / `HTTPError`, `_logger.exception(...)`, re-raise `UserError` (or `ValidationError` in payment flows). Raw `requests` exception never reaches the user.
- API-level error inside a 2xx body handled separately from HTTP status (`if "error" in resp: ...`).
- Redact the key before logging if the payload/headers are ever dumped.

## What key-auth does NOT need

No JWT, no token exchange, no refresh, no expiry cache, no per-record auth state. If the design grows those, the API is not key-based — see [api-client-oauth.md](./api-client-oauth.md).
