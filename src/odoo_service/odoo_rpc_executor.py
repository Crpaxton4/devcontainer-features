import logging
import threading
import xmlrpc.client
from typing import Any, Optional

from ._error_mapping import (
    map_authentication_failure,
    map_authentication_fault,
    map_fault,
    map_transport_error,
)
from .errors import OdooError
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

        # Track authentication as an explicit boolean flag rather than
        # relying on `None` identity checks. This makes the auth state
        # clearer and less susceptible to `is`/`is not` mutation operators.
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
                    try:
                        auth_result = self._common.authenticate(
                            self.db, self.username, self.password, {}
                        )
                    except xmlrpc.client.Fault as exc:
                        raise map_authentication_fault(exc) from exc
                    except Exception as exc:
                        raise map_transport_error(
                            exc,
                            operation="authenticate",
                        ) from exc

                    # Odoo returns `False` for invalid credentials.
                    if auth_result is False or auth_result is None:
                        raise map_authentication_failure(
                            detail="Odoo returned a falsey authentication response"
                        )

                    try:
                        authenticated_uid = int(auth_result)  # type: ignore[arg-type]
                    except (TypeError, ValueError) as exc:
                        raise map_transport_error(
                            exc,
                            operation="authenticate",
                            detail=f"Unexpected auth response from Odoo: {auth_result!r}",
                        ) from exc

                    self._uid = authenticated_uid
                    self._authenticated = True

        if self._uid is None:
            raise map_transport_error(
                RuntimeError("Authentication succeeded without a user id"),
                operation="authenticate",
            )
        return self._uid

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        """Executes a method on an Odoo model over XML-RPC."""
        _logger.debug("Executing XML-RPC call %s.%s", model, method)
        try:
            return self._object.execute_kw(
                self.db, self.uid, self.password, model, method, list(args), kwargs
            )
        except OdooError:
            raise
        except xmlrpc.client.Fault as exc:
            raise map_fault(exc, model=model, method=method) from exc
        except Exception as exc:
            raise map_transport_error(exc, model=model, method=method) from exc
