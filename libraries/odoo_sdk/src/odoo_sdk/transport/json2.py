from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any, Callable, Optional

from odoo_sdk.state.config import (
    DEFAULT_TIMEOUT_SECONDS as DEFAULT_REQUEST_TIMEOUT_SECONDS,
)

from ._credential_refresh import (
    ROTATION_HINT_NO_REFRESH,
    ROTATION_HINT_REFRESHED,
    call_refresh,
    with_rotation_hint,
)
from ._http_error_mapping import map_http_error
from .errors import OdooAuthenticationError, OdooTransportError
from .executor import OdooExecutor

if TYPE_CHECKING:  # pragma: no cover
    from odoo_sdk.state.config import OdooConnectionSettings

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
        *,
        credentials_refresh: Optional[Callable[[], "OdooConnectionSettings"]] = None,
    ) -> None:
        """Store the URL, optional database name, API key, and timeout for each request.

        ``credentials_refresh``, when given, is a zero-arg callable that
        re-resolves connection settings; on an authentication failure the
        executor invokes it and retries once when the settings actually changed
        (issue #658), so a key rotated on disk is picked up without a restart.

        The settings live in one tuple replaced atomically by ``_apply_settings``
        and snapshotted once per request by ``_request``, so a request can never
        mix fields from two settings generations (e.g. a fresh key sent to a
        stale URL) even when another thread refreshes concurrently.
        """
        self._settings: tuple[str, str | None, str, float] = (
            url.rstrip("/"),
            db,
            api_key,
            timeout,
        )
        self._credentials_refresh = credentials_refresh

    @property
    def _url(self) -> str:
        """Return the current-generation base URL."""
        return self._settings[0]

    @property
    def _db(self) -> str | None:
        """Return the current-generation database name, if any."""
        return self._settings[1]

    @property
    def _api_key(self) -> str:
        """Return the current-generation API key."""
        return self._settings[2]

    @property
    def _timeout(self) -> float:
        """Return the current-generation per-request timeout."""
        return self._settings[3]

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        """Execute one model method over the Odoo JSON-2 HTTP API.

        Positional args are converted to the named body fields JSON-2 requires,
        per :data:`_POSITIONAL_BODY_FIELDS`; keyword args become top-level fields
        and win over a positional of the same name. HTTP-error responses are mapped
        to the SDK error taxonomy by :func:`._http_error_mapping.map_http_error`
        (see that module for the status/name table).

        A rejected bearer token surfaces as a mapped authentication error, so on
        that failure the settings are re-resolved through ``credentials_refresh``
        and the request re-issued once when they changed (issue #658). Re-issuing
        is safe even for writes: an authentication-rejected request was never
        executed server-side.

        :raises OdooError: A mapped subclass for an HTTP-error response body.
        :raises OdooTransportError: On more positional args than the method has
            body fields, a non-JSON response, or a network-level error.
        """
        body: dict[str, Any] = {}
        body["context"] = kwargs.pop("context", {})
        body.update(_positional_body_fields(model, method, args))
        body.update(kwargs)

        try:
            return self._request(model, method, body)
        except OdooAuthenticationError as first:
            fresh = call_refresh(self._credentials_refresh)
            if fresh is None or not self._apply_settings(fresh):
                raise with_rotation_hint(first, self._rotation_hint()) from first
            # Headers are rebuilt inside ``_request``, so the single retry
            # carries the freshly applied bearer key. A second authentication
            # failure propagates unretried.
            return self._request(model, method, body)

    def _rotation_hint(self) -> str:
        """Return the rotation hint matching whether a refresh hook is wired."""
        if self._credentials_refresh is not None:
            return ROTATION_HINT_REFRESHED
        return ROTATION_HINT_NO_REFRESH

    def _apply_settings(self, settings: "OdooConnectionSettings") -> bool:
        """Apply freshly resolved settings; return whether anything changed.

        Compares URL, database, and API key (``_db`` may be ``None`` on direct
        construction, which tuple equality tolerates); identical settings are a
        no-op so the caller surfaces the original error instead of retrying with
        the same key. The whole tuple is replaced in one atomic assignment so a
        concurrent ``_request`` snapshot never observes a half-applied update.
        """
        current = self._settings
        fresh = (
            settings.url.rstrip("/"),
            settings.db,
            settings.api_key,
            settings.timeout,
        )
        if fresh[:3] == current[:3]:
            return False
        self._settings = fresh
        return True

    def _request(self, model: str, method: str, body: dict[str, Any]) -> Any:
        """Send one JSON-2 POST for ``body`` and decode the JSON response.

        The settings tuple is snapshotted once, so the URL, database header,
        bearer key, and timeout of one request always belong to one generation.

        :raises OdooError: A mapped subclass for an HTTP-error response body.
        :raises OdooTransportError: On a non-JSON response or a network-level error.
        """
        url, db, api_key, timeout = self._settings
        target_url = f"{url}/json/2/{model}/{method}"

        encoded = json.dumps(body).encode("utf-8")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8",
        }
        if db is not None:
            headers["X-Odoo-Database"] = db

        request = urllib.request.Request(
            target_url,
            data=encoded,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
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
