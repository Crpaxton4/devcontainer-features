import threading
import xmlrpc.client
from typing import Any

from .executor import OdooExecutor


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
    """

    def __init__(self, url: str, db: str, username: str, password: str):
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
        :return: None.
        :rtype: None
        """
        self.url = url.rstrip("/")
        self.db = db
        self.username = username
        self._password = password

        self._common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")
        self._object = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object")

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
                self._uid = self._common.authenticate(
                    self.db,
                    self.username,
                    self._password,
                    {},
                )
        # Hack to make static type checking not complain. There is likely a better way
        return int(str(self._uid))

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
        :return: Result returned by the XML-RPC endpoint.
        :rtype: Any
        """
        return self._object.execute_kw(
            self.db,
            self.uid,
            self._password,
            model,
            method,
            list(args),
            kwargs,
        )
