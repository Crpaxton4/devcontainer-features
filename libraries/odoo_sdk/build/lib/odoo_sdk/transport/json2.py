from __future__ import annotations

import json
import urllib.error
import urllib.request
from io import BytesIO
from typing import Any

from odoo_sdk.state.config import DEFAULT_TIMEOUT_SECONDS as DEFAULT_REQUEST_TIMEOUT_SECONDS

from ._http_error_mapping import map_http_error
from .errors import OdooTransportError
from .executor import OdooExecutor

# ``DEFAULT_REQUEST_TIMEOUT_SECONDS`` is re-exported from the single source
# ``odoo_sdk.state.config.DEFAULT_TIMEOUT_SECONDS`` (imported above) so the
# settings layer and both transports share one number by reference, not by copy.


class OdooJson2Executor(OdooExecutor):
    """Execute Odoo operations over the JSON-2 HTTP API using bearer token auth.

    Uses HTTP POST with a bearer token instead of XML-RPC credentials. ``db``, when
    given, is sent as the ``X-Odoo-Database`` header; ``timeout`` bounds each call
    and defaults to :data:`DEFAULT_REQUEST_TIMEOUT_SECONDS`.
    """

    def __init__(
        self,
        url: str,
        db: str | None,
        api_key: str,
        timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        """Store the URL, optional database name, API key, and timeout for each request."""
        self._url = url.rstrip("/")
        self._db = db
        self._api_key = api_key
        self._timeout = timeout

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        """Execute one model method over the Odoo JSON-2 HTTP API.

        The first positional arg, when present, is sent as ``ids``; keyword args
        become top-level fields in the JSON body. HTTP-error responses are mapped to
        the SDK error taxonomy by :func:`._http_error_mapping.map_http_error` (see
        that module for the status/name table).

        :raises OdooError: A mapped subclass for an HTTP-error response body.
        :raises OdooTransportError: On a non-JSON response or a network-level error.
        """
        target_url = f"{self._url}/json/2/{model}/{method}"

        body: dict[str, Any] = {}
        body["context"] = kwargs.pop("context", {})
        if args:
            body["ids"] = args[0]
        body.update(kwargs)

        encoded = json.dumps(body).encode("utf-8")

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json; charset=utf-8",
        }
        if self._db is not None:
            headers["X-Odoo-Database"] = self._db

        request = urllib.request.Request(
            target_url,
            data=encoded,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            raise map_http_error(exc.code, raw, model=model, method=method) from None
        except urllib.error.URLError as exc:
            raise OdooTransportError(
                "Transport error communicating with Odoo server",
                model=model,
                method=method,
                detail=str(exc.reason),
            ) from exc

        try:
            return json.loads(raw)
        except ValueError:
            raise OdooTransportError(
                "Non-JSON response received from server",
                model=model,
                method=method,
                detail=raw[:500],
            )
