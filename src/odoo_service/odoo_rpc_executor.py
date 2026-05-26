import logging
import threading
import xmlrpc.client
from typing import Any, Optional

from .odoo_executor import OdooExecutor

_logger = logging.getLogger(__name__)


class OdooRpcExecutor(OdooExecutor):
    """Handles authentication and XML-RPC execution against an Odoo server."""

    def __init__(self, url: str, db: str, username: str, password: str):
        _logger.info("Creating OdooRpcExecutor for db=%s", db)
        self.url = url.rstrip("/")
        self.db = db
        self.username = username
        self.password = password

        self._common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")
        self._object = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object")

        self._uid: Optional[int] = None
        self._authenticated = False
        self._lock = threading.Lock()

    @property
    def uid(self) -> int:
        """Lazily authenticates and returns the user ID."""
        if not self._authenticated:
            with self._lock:
                if not self._authenticated:
                    _logger.info("Authenticating against Odoo for db=%s", self.db)
                    auth_result = self._common.authenticate(
                        self.db, self.username, self.password, {}
                    )
                    self._uid = int(auth_result)
                    self._authenticated = True

        return self._uid  # type: ignore[return-value]

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        """Executes a method on an Odoo model over XML-RPC."""
        _logger.debug("Executing XML-RPC call %s.%s", model, method)
        return self._object.execute_kw(
            self.db, self.uid, self.password, model, method, list(args), kwargs
        )
