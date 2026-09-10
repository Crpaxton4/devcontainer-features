# API Client — OAuth Integration

Odoo calls OUT to a 3rd-party API behind OAuth2. Credential is NOT static: a long-lived refresh token mints short-lived access tokens that expire and must be renewed. Adds an auth-state + refresh tier on top of plain transport.

Outline only. Structural skeleton, not a mandate. Adapt to the provider.

## Two-tier split (observed base pattern)

Base does this exact thing in `google.gmail.mixin` + `google.service` (and `microsoft_outlook` mirrors it):

- **Auth/state tier — a mixin bolted onto the record** that owns the account. Holds the token fields ON the record, the reuse-or-refresh logic, and the token-exchange HTTP call (that one call is inline because it reads the record's own fields).
- **Transport tier — a stateless `AbstractModel` service** (`google.service._do_request`) owning the raw `requests` boundary for the DATA API. Called by name, inherited by no one.

Auth token exchange is the ONLY `requests` call that belongs in the record-inherited mixin. All data-API transport stays in the service.

## App-level secrets vs per-account tokens

- App `client_id` / `client_secret` → `ir.config_parameter` (global, one per Odoo instance).
- Per-account `refresh_token` / `access_token` / expiry → fields ON the record that owns the connection (`ir.mail_server`, `fetchmail.server`, or your model), all `groups="base.group_system", copy=False`.

```python
class XOauthMixin(models.AbstractModel):
    _name = "x.oauth.mixin"
    _description = "X OAuth Mixin"

    x_refresh_token = fields.Char(groups="base.group_system", copy=False)
    x_access_token = fields.Char(groups="base.group_system", copy=False)
    x_access_token_expiration = fields.Integer(groups="base.group_system", copy=False)
```

## Token lifecycle

```python
def _fetch_token(self, grant_type, **values):
    """One call for both authorization_code and refresh_token grants."""
    Config = self.env["ir.config_parameter"].sudo()
    resp = requests.post(TOKEN_ENDPOINT, data={
        "client_id": Config.get_param("x_client_id"),
        "client_secret": Config.get_param("x_client_secret"),
        "grant_type": grant_type,
        **values,
    }, timeout=TOKEN_TIMEOUT)
    if not resp.ok:
        raise UserError(_("Could not fetch the access token."))
    return resp.json()

def _get_access_token(self):
    """Reuse the cached access token; refresh only when near expiry."""
    self.ensure_one()
    now = int(time.time())
    if (not self.x_access_token
            or not self.x_access_token_expiration
            or self.x_access_token_expiration - EXPIRY_THRESHOLD < now):
        resp = self._fetch_token("refresh_token", refresh_token=self.x_refresh_token)
        self.write({
            "x_access_token": resp["access_token"],
            "x_access_token_expiration": now + resp["expires_in"],
        })
    return self.x_access_token
```

## Authorization flow (initial consent)

- Compute an auth-URL field (`_compute_*_uri`) from `client_id` + redirect URI + scope + `state`.
- Put `access_type=offline` + `prompt=consent` (or provider equivalent) so a REFRESH token is returned, not just an access token.
- `state` carries `{model, id, csrf_token}`; verify the CSRF token in the callback controller (`tools.misc.hmac`).
- An `ir.actions.act_url` action opens the consent page; force a form save first so the record exists in DB before its id goes in the URL.
- A `/x/confirm` controller receives the `code`, exchanges it via `_fetch_token("authorization_code", code=...)`, stores the refresh token.

## Common structural elements

- HTTP lib = `requests`, `timeout=` on every call (token AND data).
- Refresh with a safety margin (`EXPIRY_THRESHOLD`): renew slightly BEFORE actual expiry so a token never dies mid-request.
- Reuse-then-refresh: cache the access token on the record, only hit the token endpoint when stale.
- Cache access token + expiry as a PAIR (write both together; a lone one = broken cache).
- Only `base.group_system` reads tokens; consent action guarded by the same group.
- Data-API calls go through the transport tier ([api-client-key.md](./api-client-key.md) transport skeleton), using `Authorization: Bearer <access_token>` from `_get_access_token()`.
- Redact `client_secret` / tokens before any request logging.
