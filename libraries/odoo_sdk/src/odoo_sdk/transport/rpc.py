import http.client
import socket
import threading
import xmlrpc.client
from typing import TYPE_CHECKING, Any, Callable, Optional, TypeVar
from urllib.parse import urlsplit

from odoo_sdk.state.config import (
    DEFAULT_TIMEOUT_SECONDS as DEFAULT_REQUEST_TIMEOUT_SECONDS,
)

from ._credential_refresh import (
    ROTATION_HINT_NO_REFRESH,
    ROTATION_HINT_REFRESHED,
    call_refresh,
    with_rotation_hint,
)
from ._fault_mapping import map_xmlrpc_fault
from .errors import OdooAuthenticationError, OdooTransportError
from .executor import OdooExecutor

if TYPE_CHECKING:  # pragma: no cover
    from odoo_sdk.state.config import OdooConnectionSettings

_T = TypeVar("_T")

# ``DEFAULT_REQUEST_TIMEOUT_SECONDS`` is re-exported from the single source
# ``odoo_sdk.state.config.DEFAULT_TIMEOUT_SECONDS`` (imported above) so the
# settings layer and both transports share one number by reference, not by copy.


def _mapped_call(
    operation: Callable[[], _T],
    *,
    model: Optional[str],
    method: Optional[str],
) -> _T:
    """Run ``operation`` translating XML-RPC failures into the SDK taxonomy.

    Both authentication and ``execute_kw`` cross the XML-RPC boundary and classify
    failures identically: a server-side :class:`xmlrpc.client.Fault` becomes a
    mapped :class:`OdooError`, while client-side protocol, timeout, and connectivity
    failures become an :class:`OdooTransportError`.
    """
    try:
        return operation()
    except xmlrpc.client.Fault as fault:
        raise map_xmlrpc_fault(fault, model=model, method=method) from fault
    except (
        xmlrpc.client.ProtocolError,
        socket.timeout,
        http.client.HTTPException,
        OSError,
    ) as exc:
        raise OdooTransportError(
            "Transport error communicating with Odoo server",
            model=model,
            method=method,
            detail=str(exc),
        ) from exc


class _TimeoutMixin:
    """Bound every XML-RPC connection with an explicit socket timeout.

    :class:`xmlrpc.client.ServerProxy` exposes no timeout parameter, so the
    connection must be created with one to bound how long a call blocks on a slow
    or hung server. Mixed ahead of the HTTP :class:`~xmlrpc.client.Transport` or
    HTTPS :class:`~xmlrpc.client.SafeTransport` base by the two subclasses below.
    """

    def __init__(self, timeout: float) -> None:
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host: Any) -> http.client.HTTPConnection:
        connection = super().make_connection(host)
        connection.timeout = self._timeout
        return connection


class _TimeoutTransport(_TimeoutMixin, xmlrpc.client.Transport):
    """Timeout-bounded XML-RPC transport for plain HTTP endpoints."""


class _SafeTimeoutTransport(_TimeoutMixin, xmlrpc.client.SafeTransport):
    """Timeout-bounded XML-RPC transport for HTTPS endpoints."""


def _make_timeout_transport(url: str, timeout: float) -> xmlrpc.client.Transport:
    """Build a timeout-bounded XML-RPC transport matching the URL scheme (HTTPS vs HTTP)."""
    if urlsplit(url).scheme == "https":
        return _SafeTimeoutTransport(timeout)
    return _TimeoutTransport(timeout)


class OdooRpcExecutor(OdooExecutor):
    """Execute Odoo operations over the XML-RPC endpoints, authenticating lazily.

    The SDK's default transport: it uses Odoo's external XML-RPC API and defers the
    login handshake until the first ``execute_kw`` call. ``timeout`` bounds each
    call and defaults to :data:`DEFAULT_REQUEST_TIMEOUT_SECONDS`.
    """

    def __init__(
        self,
        url: str,
        db: str,
        username: str,
        password: str,
        timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        *,
        credentials_refresh: Optional[Callable[[], "OdooConnectionSettings"]] = None,
    ):
        """Set up persistent common/object endpoint proxies and a lazy uid cache.

        ``credentials_refresh``, when given, is a zero-arg callable that
        re-resolves connection settings; on an authentication failure the
        executor invokes it and retries once when the settings actually changed
        (issue #658), so a key rotated on disk is picked up without a restart.
        """
        self.url = url.rstrip("/")
        self.db = db
        self.username = username
        self._password = password
        self._timeout = timeout
        self._credentials_refresh = credentials_refresh

        self._build_proxies()

        self._uid: Optional[int] = None
        self._lock = threading.Lock()

    def _build_proxies(self) -> None:
        """(Re)build the persistent common/object endpoint proxies."""
        self._common = xmlrpc.client.ServerProxy(
            f"{self.url}/xmlrpc/2/common",
            transport=_make_timeout_transport(self.url, self._timeout),
        )
        self._object = xmlrpc.client.ServerProxy(
            f"{self.url}/xmlrpc/2/object",
            transport=_make_timeout_transport(self.url, self._timeout),
        )

    @property
    def uid(self) -> int:
        """Authenticate lazily and return the Odoo user id.

        A successful login caches the real user id; a rejected login is never cached
        so callers may retry after correcting their credentials. The cache is
        re-checked inside the lock (double-checked locking) so concurrent first
        callers perform exactly one login handshake: without the inner check, a
        thread that queued behind the winner would re-authenticate on wake-up.

        :raises OdooAuthenticationError: When Odoo rejects the credentials.
        :raises OdooTransportError: On a protocol, timeout, or connectivity failure.
        """
        if self._uid is None:
            with self._lock:
                if self._uid is None:
                    self._uid = self._authenticate_with_refresh()
        return self._uid

    def _rotation_hint(self) -> str:
        """Return the rotation hint matching whether a refresh hook is wired."""
        if self._credentials_refresh is not None:
            return ROTATION_HINT_REFRESHED
        return ROTATION_HINT_NO_REFRESH

    def _apply_settings(self, settings: "OdooConnectionSettings") -> bool:
        """Apply freshly resolved settings; return whether anything changed.

        Must be called only while holding ``self._lock``. A change to the URL,
        database, username, or password updates the connection state (including
        the timeout) and rebuilds both endpoint proxies; identical settings are
        a no-op so the caller can surface the original error instead of
        retrying with the same credentials.
        """
        fresh = (
            settings.url.rstrip("/"),
            settings.db,
            settings.username,
            settings.password,
        )
        if fresh == (self.url, self.db, self.username, self._password):
            return False
        self.url, self.db, self.username, self._password = fresh
        self._timeout = settings.timeout
        self._build_proxies()
        return True

    def _authenticate_with_refresh(self) -> int:
        """Authenticate, re-resolving settings and retrying once on rejection.

        Runs under ``self._lock`` (:meth:`uid` acquires it); ``_authenticate``
        never touches the lock, so there is no re-entrancy. On a rejected login
        the refresh hook is consulted: unchanged, absent, or failing settings
        surface the original error with the rotation hint appended, while
        changed settings earn exactly one further attempt whose failure
        propagates plain — no loop, no recursion.
        """
        try:
            return self._authenticate()
        except OdooAuthenticationError as first:
            fresh = call_refresh(self._credentials_refresh)
            if fresh is None or not self._apply_settings(fresh):
                raise with_rotation_hint(first, self._rotation_hint()) from first
            return self._authenticate()

    def _authenticate(self) -> int:
        """Perform the XML-RPC login handshake and validate the returned user id.

        A valid login yields a positive integer user id; a rejected login yields a
        falsy or non-integer value that must surface as an explicit authentication
        failure. Booleans are rejected explicitly because ``bool`` subclasses ``int``,
        so a server returning ``True`` would otherwise masquerade as ``uid=1``. The
        password is excluded from the error message to avoid leaking the credential.

        :raises OdooAuthenticationError: When the credentials are rejected.
        :raises OdooTransportError: On a protocol, timeout, or connectivity failure.
        """
        result = _mapped_call(
            lambda: self._common.authenticate(
                self.db,
                self.username,
                self._password,
                {},
            ),
            model=None,
            method="authenticate",
        )
        if isinstance(result, bool) or not isinstance(result, int) or result <= 0:
            raise OdooAuthenticationError(
                f"Odoo authentication failed for user {self.username!r} "
                f"on database {self.db!r}",
                operation="authenticate",
                method="authenticate",
            )
        return result

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        """Execute one model method over Odoo's ``execute_kw`` XML-RPC API.

        A rotated password with a cached uid surfaces here as an
        ``AccessDenied``-marked ``execute_kw`` fault, so on an authentication
        failure the settings are re-resolved and the call re-issued once when
        they changed (issue #658). Re-issuing is safe even for writes: an
        ``AccessDenied``-rejected call was never executed server-side.

        :raises OdooError: When the server returns an XML-RPC fault.
        :raises OdooTransportError: On a protocol, timeout, or connectivity failure.
        """
        # The uid read stays outside the try: a first-login rejection is
        # already refresh-handled (and hinted) by ``_authenticate_with_refresh``.
        uid = self.uid
        try:
            return self._call_execute_kw(uid, model, method, args, kwargs)
        except OdooAuthenticationError as exc:
            fresh = call_refresh(self._credentials_refresh)
            with self._lock:
                self._uid = None
                changed = fresh is not None and self._apply_settings(fresh)
            if not changed:
                raise with_rotation_hint(exc, self._rotation_hint()) from exc
            # Outside the lock: re-reading ``self.uid`` re-authenticates with
            # the applied settings. That read may consult the refresh hook once
            # more, but the settings are already applied so it resolves
            # unchanged and fails fast — bounded, and a second authentication
            # failure propagates unretried.
            return self._call_execute_kw(self.uid, model, method, args, kwargs)

    def _call_execute_kw(
        self,
        uid: int,
        model: str,
        method: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        """Issue one ``execute_kw`` call with the given uid and current credentials."""
        return _mapped_call(
            lambda: self._object.execute_kw(
                self.db,
                uid,
                self._password,
                model,
                method,
                list(args),
                kwargs,
            ),
            model=model,
            method=method,
        )
