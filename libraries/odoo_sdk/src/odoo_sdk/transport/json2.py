from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from odoo_sdk.state.config import DEFAULT_TIMEOUT_SECONDS as DEFAULT_REQUEST_TIMEOUT_SECONDS

from ._http_error_mapping import map_http_error
from .errors import OdooTransportError
from .executor import OdooExecutor

# ``DEFAULT_REQUEST_TIMEOUT_SECONDS`` is re-exported from the single source
# ``odoo_sdk.state.config.DEFAULT_TIMEOUT_SECONDS`` (imported above) so the
# settings layer and both transports share one number by reference, not by copy.

# JSON-2 is named-arguments-only (Phase E "Named Arguments Only" decision), while
# every Phase A-D recordset op calls ``execute`` with the XML-RPC positional
# convention -- ``write(ids, vals)``, ``search(domain)``, ``read_group(domain,
# fields, groupby)``. This table is the positional-to-named conversion the
# contract requires the executor to perform: each entry lists, in order, the JSON
# body field each positional argument belongs in. ``ids`` is the recordset the
# method is bound to; every other name mirrors the server-side method signature
# the JSON-2 dispatcher binds against.
_POSITIONAL_BODY_FIELDS: dict[str, tuple[str, ...]] = {
    "copy": ("ids", "default"),
    "create": ("vals_list",),
    "default_get": ("fields_list",),
    "fields_get": ("allfields", "attributes"),
    "get_metadata": ("ids",),
    "name_create": ("name",),
    "name_search": ("name", "domain", "operator", "limit"),
    "read": ("ids", "fields"),
    "read_group": (
        "domain",
        "fields",
        "groupby",
        "offset",
        "limit",
        "orderby",
        "lazy",
    ),
    "search": ("domain", "offset", "limit", "order"),
    "search_count": ("domain", "limit"),
    "search_read": ("domain", "fields", "offset", "limit", "order"),
    "write": ("ids", "vals"),
}

# Methods outside the table are arbitrary model methods invoked with the same
# leading-recordset convention (e.g. ``message_post([task_id], body=...)``), so a
# lone positional argument is the id list and anything further must be named.
_DEFAULT_POSITIONAL_BODY_FIELDS: tuple[str, ...] = ("ids",)


def _positional_body_fields(
    model: str, method: str, args: tuple[Any, ...]
) -> dict[str, Any]:
    """Convert positional call arguments into their named JSON-2 body fields.

    :raises OdooTransportError: When *method* was given more positional arguments
        than JSON-2 has body fields for, which previously dropped them silently.
    """
    names = _POSITIONAL_BODY_FIELDS.get(method, _DEFAULT_POSITIONAL_BODY_FIELDS)
    if len(args) > len(names):
        raise OdooTransportError(
            "Too many positional arguments for a JSON-2 request",
            model=model,
            method=method,
            detail=(
                f"JSON-2 maps at most {len(names)} positional argument(s) for "
                f"'{method}' ({', '.join(names)}), but {len(args)} were given; "
                "pass the remaining arguments as keyword arguments."
            ),
        )
    return dict(zip(names, args))


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

        Positional args are converted to the named body fields JSON-2 requires,
        per :data:`_POSITIONAL_BODY_FIELDS`; keyword args become top-level fields
        and win over a positional of the same name. HTTP-error responses are mapped
        to the SDK error taxonomy by :func:`._http_error_mapping.map_http_error`
        (see that module for the status/name table).

        :raises OdooError: A mapped subclass for an HTTP-error response body.
        :raises OdooTransportError: On more positional args than the method has
            body fields, a non-JSON response, or a network-level error.
        """
        target_url = f"{self._url}/json/2/{model}/{method}"

        body: dict[str, Any] = {}
        body["context"] = kwargs.pop("context", {})
        body.update(_positional_body_fields(model, method, args))
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
