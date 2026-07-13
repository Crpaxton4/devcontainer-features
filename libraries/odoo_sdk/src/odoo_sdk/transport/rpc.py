import http.client
import socket
import threading
import xmlrpc.client
from typing import Any, Callable, Optional, TypeVar
from urllib.parse import urlsplit

from ._fault_mapping import map_xmlrpc_fault
from .errors import OdooTransportError
from .executor import OdooExecutor

_T = TypeVar("_T")


def _mapped_call(
    operation: Callable[[], _T],
    *,
    model: Optional[str],
    method: Optional[str],
) -> _T:
    """Run ``operation`` translating XML-RPC failures into the SDK taxonomy.

    This helper is necessary because both authentication and ``execute_kw`` cross the
    XML-RPC boundary and must classify failures identically: a server-side
    :class:`xmlrpc.client.Fault` becomes a mapped :class:`OdooError`, while client-side
    protocol, timeout, and connectivity failures become an :class:`OdooTransportError`.
    Sharing one wrapper keeps the two call sites composable.

    :param operation: Zero-argument callable performing the XML-RPC request.
    :type operation: Callable[[], _T]
    :param model: Odoo model name involved in the call, or None when not applicable.
    :type model: Optional[str]
    :param method: Odoo method name involved in the call, or None when not applicable.
    :type method: Optional[str]
    :raises OdooError: When the server returns an XML-RPC fault, classified into the
        appropriate subclass.
    :raises OdooTransportError: When a protocol, timeout, or connectivity failure
        occurs before a server fault could be produced.
    :return: The value returned by ``operation``.
    :rtype: _T
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

#: Default per-request timeout, in seconds, applied to every XML-RPC call.
#:
#: This bounds the time the SDK will block on a hung or slow Odoo server so that a
#: stalled socket surfaces as an error instead of hanging the caller forever.
DEFAULT_REQUEST_TIMEOUT_SECONDS: float = 30.0


class _TimeoutTransport(xmlrpc.client.Transport):
    """XML-RPC HTTP transport that bounds each connection with a socket timeout.

    This transport is necessary because :class:`xmlrpc.client.ServerProxy` offers no
    direct timeout parameter, so the connection must be created with an explicit
    ``timeout`` to bound how long a call may block on a slow or hung server.

    :param timeout: Per-request timeout in seconds.
    :type timeout: float
    """

    def __init__(self, timeout: float) -> None:
        """Store the timeout used when opening each HTTP connection.

        :param timeout: Per-request timeout in seconds.
        :type timeout: float
        :return: None.
        :rtype: None
        """
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host: Any) -> http.client.HTTPConnection:
        """Create a connection whose socket is bounded by the configured timeout.

        :param host: Host specification passed by the XML-RPC machinery.
        :type host: Any
        :return: A connection with the configured socket timeout applied.
        :rtype: http.client.HTTPConnection
        """
        connection = super().make_connection(host)
        connection.timeout = self._timeout
        return connection


class _SafeTimeoutTransport(xmlrpc.client.SafeTransport):
    """XML-RPC HTTPS transport that bounds each connection with a socket timeout.

    This transport is necessary because HTTPS endpoints use
    :class:`xmlrpc.client.SafeTransport`, which likewise exposes no timeout
    parameter, so the connection must be created with an explicit ``timeout``.

    :param timeout: Per-request timeout in seconds.
    :type timeout: float
    """

    def __init__(self, timeout: float) -> None:
        """Store the timeout used when opening each HTTPS connection.

        :param timeout: Per-request timeout in seconds.
        :type timeout: float
        :return: None.
        :rtype: None
        """
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host: Any) -> http.client.HTTPSConnection:
        """Create a connection whose socket is bounded by the configured timeout.

        :param host: Host specification passed by the XML-RPC machinery.
        :type host: Any
        :return: A connection with the configured socket timeout applied.
        :rtype: http.client.HTTPSConnection
        """
        connection = super().make_connection(host)
        connection.timeout = self._timeout
        return connection


def _make_timeout_transport(url: str, timeout: float) -> xmlrpc.client.Transport:
    """Build a timeout-bounded XML-RPC transport matching the URL scheme.

    A scheme-aware factory is necessary because HTTPS endpoints require a
    :class:`xmlrpc.client.SafeTransport` subclass while plain HTTP uses the base
    :class:`xmlrpc.client.Transport`; both need the socket timeout applied.

    :param url: Base URL of the Odoo server, used to select HTTP vs HTTPS.
    :type url: str
    :param timeout: Per-request timeout in seconds.
    :type timeout: float
    :return: A transport that bounds each connection with the given timeout.
    :rtype: xmlrpc.client.Transport
    """
    if urlsplit(url).scheme == "https":
        return _SafeTimeoutTransport(timeout)
    return _TimeoutTransport(timeout)


class OdooRpcExecutor(OdooExecutor):
    """Execute Odoo operations over the XML-RPC endpoints.

    This executor is necessary because the SDK's default transport uses Odoo's
    external XML-RPC API and must lazily authenticate before issuing `execute_kw`
    calls against model methods.

    :param url: Base URL of the Odoo server.
    :type url: str
    :param db: Database name to authenticate against.
    :type db: str
    :param username: Username used for authentication.
    :type username: str
    :param password: Password or API key used for authentication.
    :type password: str
    :param timeout: Per-request timeout in seconds. Defaults to
        :data:`DEFAULT_REQUEST_TIMEOUT_SECONDS`.
    :type timeout: float
    """

    def __init__(
        self,
        url: str,
        db: str,
        username: str,
        password: str,
        timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ):
        """Initialize XML-RPC proxies and deferred authentication state.

        The constructor is necessary because the transport needs persistent common
        and object endpoint proxies plus a lock-protected uid cache for later calls.

        :param url: Base URL of the Odoo server.
        :type url: str
        :param db: Database name to authenticate against.
        :type db: str
        :param username: Username used for authentication.
        :type username: str
        :param password: Password or API key used for authentication.
        :type password: str
        :param timeout: Per-request timeout in seconds bounding how long each call may
            block on a slow or hung server. Defaults to
            :data:`DEFAULT_REQUEST_TIMEOUT_SECONDS`.
        :type timeout: float
        :return: None.
        :rtype: None
        """
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

        self._uid = None
        self._lock = threading.Lock()

    @property
    def uid(self) -> int:
        """Authenticate lazily and return the Odoo user id.

        Lazy authentication is necessary because many callers construct the executor
        before they actually issue a request, and repeated calls should not repeat
        the login handshake.

        :return: Authenticated Odoo user id. 0 if authentication fails.
        :rtype: int
        """

        if self._uid is None:
            with self._lock:
                self._uid = _mapped_call(
                    lambda: self._common.authenticate(
                        self.db,
                        self.username,
                        self._password,
                        {},
                    ),
                    model=None,
                    method="authenticate",
                )
        # Hack to make static type checking not complain. There is likely a better way
        return int(str(self._uid)) if self._uid else -1

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        """Execute one model method over Odoo's `execute_kw` XML-RPC API.

        This method is necessary because all higher-level SDK abstractions converge on
        one transport call shape when they finally cross the server boundary.

        :param model: Name of the Odoo model to call.
        :type model: str
        :param method: Name of the Odoo method to invoke.
        :type method: str
        :param args: Positional arguments forwarded to `execute_kw`.
        :type args: Any
        :param kwargs: Keyword arguments forwarded to `execute_kw`.
        :type kwargs: Any
        :raises OdooError: When the server returns an XML-RPC fault, classified into
            the appropriate subclass of :class:`OdooError`.
        :raises OdooTransportError: When a protocol, timeout, or connectivity failure
            occurs while issuing the call.
        :return: Result returned by the XML-RPC endpoint.
        :rtype: Any
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
