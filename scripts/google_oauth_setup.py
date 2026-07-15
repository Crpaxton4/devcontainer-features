#!/usr/bin/env python3
"""Host-side OAuth helper that mints a Google token for the tracker (issue #370).

The odoo-sdk inside the dev container ingests the user's Google Calendar meetings
and SENT Gmail as resync event sources, but — like the tracker database itself
(#369) — it never runs an OAuth flow and never mints credentials. Credentials are
HOST-PROVISIONED: this script runs the Google installed-app / loopback OAuth flow
on the host and writes the resulting token JSON into the existing
``~/.config/odoo_sdk`` mount, from where the container CONSUMES it (refreshing via
a plain token-endpoint POST when the access token goes stale).

Run it once on the host, supplying the OAuth client id/secret you created in the
Google Cloud console (an "OAuth client ID" of type "Desktop app")::

    python3 scripts/google_oauth_setup.py \\
        --client-id  <CLIENT_ID>.apps.googleusercontent.com \\
        --client-secret <CLIENT_SECRET> \\
        --output ~/.config/odoo_sdk/google_token.json

Scopes requested (both READ-ONLY):

* ``https://www.googleapis.com/auth/calendar.readonly``
* ``https://www.googleapis.com/auth/gmail.readonly``

It opens the consent screen in your browser and captures the redirect on an
ephemeral ``http://localhost:<port>`` loopback, then exchanges the code for a
refresh token and writes ``{client_id, client_secret, refresh_token, token,
token_uri, scopes, expiry}`` to ``--output`` with ``0600`` permissions.

Stdlib-only by design: it runs on a bare host with no ``odoo_sdk`` and no
third-party Google libraries installed — everything here is ``urllib`` /
``http.server`` / ``secrets``.
"""

import argparse
import http.server
import json
import os
import secrets
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_SCOPES = (
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
)
_DEFAULT_OUTPUT = "~/.config/odoo_sdk/google_token.json"


def _free_loopback_port() -> int:
    """Return an OS-assigned free TCP port on the loopback interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _CodeCatcher(http.server.BaseHTTPRequestHandler):
    """One-shot handler capturing the ``?code=...`` on the loopback redirect."""

    code: Optional[str] = None
    state: Optional[str] = None

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _CodeCatcher.code = (params.get("code") or [None])[0]
        _CodeCatcher.state = (params.get("state") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        body = "Authorization received. You can close this tab and return to the terminal."
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *_args) -> None:  # silence the default stderr logging
        return


def _build_auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    """Build the consent-screen URL requesting offline read-only access."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{_AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"


def _capture_code(redirect_uri: str, auth_url: str, port: int) -> str:
    """Open the consent screen and block until the loopback receives the code."""
    print(f"Opening the Google consent screen; if it does not open, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)
    server = http.server.HTTPServer(("127.0.0.1", port), _CodeCatcher)
    try:
        server.handle_request()  # serve exactly one redirect
    finally:
        server.server_close()
    if not _CodeCatcher.code:
        raise SystemExit("ERROR: no authorization code was received on the loopback.")
    return _CodeCatcher.code


def _exchange_code(
    code: str, client_id: str, client_secret: str, redirect_uri: str
) -> dict:
    """Exchange the authorization code for access/refresh tokens."""
    payload = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode()
    request = urllib.request.Request(_TOKEN_ENDPOINT, data=payload, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"ERROR: token exchange failed ({exc.code}): {detail}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"ERROR: token exchange failed: {exc}")


def _token_document(
    tokens: dict, client_id: str, client_secret: str
) -> dict:
    """Shape the token-endpoint response into the file the SDK consumes."""
    expires_in = int(tokens.get("expires_in", 0))
    expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": tokens.get("refresh_token", ""),
        "token": tokens.get("access_token", ""),
        "token_uri": _TOKEN_ENDPOINT,
        "scopes": list(_SCOPES),
        "expiry": expiry.isoformat(),
    }


def _write_token(document: dict, output: Path) -> None:
    """Write the token JSON with ``0600`` permissions, creating parent dirs."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    os.chmod(output, 0o600)


def _parse_args(argv: list) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--client-id", required=True, help="OAuth client id")
    parser.add_argument("--client-secret", required=True, help="OAuth client secret")
    parser.add_argument(
        "--output",
        default=_DEFAULT_OUTPUT,
        help=f"Token file to write (default: {_DEFAULT_OUTPUT})",
    )
    return parser.parse_args(argv)


def main(argv: list) -> int:
    args = _parse_args(argv)
    port = _free_loopback_port()
    redirect_uri = f"http://localhost:{port}/"
    state = secrets.token_urlsafe(16)
    auth_url = _build_auth_url(args.client_id, redirect_uri, state)
    code = _capture_code(redirect_uri, auth_url, port)
    if _CodeCatcher.state != state:
        raise SystemExit("ERROR: OAuth state mismatch; aborting for safety.")
    tokens = _exchange_code(code, args.client_id, args.client_secret, redirect_uri)
    if not tokens.get("refresh_token"):
        raise SystemExit(
            "ERROR: no refresh_token returned. Revoke prior access at "
            "https://myaccount.google.com/permissions and re-run (the flow "
            "requests prompt=consent to force one)."
        )
    document = _token_document(tokens, args.client_id, args.client_secret)
    output = Path(args.output).expanduser()
    _write_token(document, output)
    print(f"ok  wrote Google token to {output} (0600)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
