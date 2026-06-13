from __future__ import annotations

import json
import urllib.error
import urllib.request
from io import BytesIO
from typing import Any

from ._http_error_mapping import map_http_error
from .errors import (
    OdooAccessError,
    OdooAuthenticationError,
    OdooError,
    OdooMissingRecordError,
    OdooServerError,
    OdooTransportError,
    OdooValidationError,
)
from .executor import OdooExecutor


class OdooJson2Executor(OdooExecutor):
    """Execute Odoo operations over the JSON-2 HTTP API using bearer token auth.

    This executor is necessary because the JSON-2 transport uses HTTP POST with a
    bearer token instead of XML-RPC credentials, and requires a distinct request
    construction and response parsing path.

    :param url: Base URL of the Odoo server.
    :type url: str
    :param db: Database name. When provided, sent as the ``X-Odoo-Database`` header.
    :type db: str | None
    :param api_key: API key used for bearer token authentication.
    :type api_key: str
    """

    def __init__(self, url: str, db: str | None, api_key: str) -> None:
        """Store connection parameters for later use in each request.

        The constructor is necessary to capture the URL, optional database name, and
        API key so that every call can produce consistent headers and a well-formed
        POST target without repeating argument threading.

        :param url: Base URL of the Odoo server.
        :type url: str
        :param db: Database name, or ``None`` to omit the database header.
        :type db: str | None
        :param api_key: Bearer token for request authentication.
        :type api_key: str
        :return: None.
        :rtype: None
        """
        self._url = url.rstrip("/")
        self._db = db
        self._api_key = api_key

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        """Execute one model method over the Odoo JSON-2 HTTP API.

        This method is necessary because all SDK operations eventually converge on one
        transport call that must produce a valid JSON-2 POST request and parse the
        response uniformly.

        :param model: Name of the Odoo model to call.
        :type model: str
        :param method: Name of the method to execute.
        :type method: str
        :param args: Positional arguments; the first element is used as ``ids`` when
            present.
        :type args: Any
        :param kwargs: Keyword arguments passed as top-level fields in the JSON body.
        :type kwargs: Any
        :raises OdooAuthenticationError: When the server returns HTTP 401 or an
            ``odoo.exceptions.AccessDenied`` JSON error.
        :raises OdooAccessError: When the server returns HTTP 403 or an
            ``odoo.exceptions.AccessError`` JSON error.
        :raises OdooMissingRecordError: When the server returns HTTP 404 or an
            ``odoo.exceptions.MissingError`` JSON error.
        :raises OdooValidationError: When the server returns HTTP 422 or an
            ``odoo.exceptions.ValidationError`` JSON error.
        :raises OdooServerError: When the server returns a mapped or unmapped 5xx
            error.
        :raises OdooTransportError: When the response body is not valid JSON or a
            network-level error occurs.
        :return: Parsed response value from the server.
        :rtype: Any
        """
        target_url = f"{self._url}/json/2/{model}/{method}"

        body: dict[str, Any] = {}
        body["context"] = kwargs.pop("context", {})
        if args:
            body["ids"] = args[0]
        body.update(kwargs)

        encoded = json.dumps(body).encode("utf-8")

        headers = {
            "Authorization": f"bearer {self._api_key}",
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
            with urllib.request.urlopen(request) as response:
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
        except (json.JSONDecodeError, ValueError):
            raise OdooTransportError(
                "Non-JSON response received from server",
                model=model,
                method=method,
                detail=raw[:500],
            )
