import http.client
import socket
import threading
import xmlrpc.client
from typing import Any, Callable, Optional, TypeVar
from urllib.parse import urlsplit

from odoo_sdk.state.config import DEFAULT_TIMEOUT_SECONDS as DEFAULT_REQUEST_TIMEOUT_SECONDS

from ._fault_mapping import map_xmlrpc_fault
from .errors import OdooAuthenticationError, OdooTransportError
from .executor import OdooExecutor

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
    ):
        """Set up persistent common/object endpoint proxies and a lazy uid cache."""
        self.url = url.rstrip("/")
        self.db = db
        self.username = username
        self._password = password

        self._common = xmlrpc.client.ServerProxy(
            f"{self.url}/xmlrpc/2/common",
            transport=_make_timeout_transport(self.url, timeout),
        )
        self._object = xmlrpc.client.ServerProxy(
            f"{self.url}/xmlrpc/2/object",
            transport=_make_timeout_transport(self.url, timeout),
        )

        self._uid: Optional[int] = None
        self._lock = threading.Lock()

    @property
    def uid(self) -> int:
        """Authenticate lazily and return the Odoo user id.

        A successful login caches the real user id; a rejected login is never cached
        so callers may retry after correcting their credentials.

        :raises OdooAuthenticationError: When Odoo rejects the credentials.
        :raises OdooTransportError: On a protocol, timeout, or connectivity failure.
        """
        if self._uid is None:
            with self._lock:
                self._uid = self._authenticate()
        return self._uid

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

        :raises OdooError: When the server returns an XML-RPC fault.
        :raises OdooTransportError: On a protocol, timeout, or connectivity failure.
        """
        uid = self.uid
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
