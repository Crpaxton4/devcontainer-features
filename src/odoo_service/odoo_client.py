import logging
import threading
from typing import Any, Dict, Optional

from .odoo_config import OdooConnectionSettings
from .odoo_env import OdooEnv
from .odoo_executor import OdooExecutor
from .odoo_model import OdooModel
from .odoo_rpc_executor import OdooRpcExecutor

_logger = logging.getLogger(__name__)


class OdooClient(OdooExecutor):
    """
    The main Odoo SDK entry point.
    Acts as a dictionary of models, handling model caching automatically.

    Usage:
        odoo = OdooClient(url, db, user, pw)
        records = odoo['res.partner'].search([('is_company','=',True)]).limit(10).read(['name'])
    """

    def __init__(
        self,
        url: Optional[str] = None,
        db: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        executor: Optional[OdooExecutor] = None,
        config_path: Optional[str] = None,
    ):
        """Initializes the Odoo client.

        :param url: The URL of the Odoo server.
        :type url: str
        :param db: The name of the Odoo database.
        :type db: str
        :param username: The username for authentication.
        :type username: str
        :param password: The password for authentication.
        :type password: str
        """
        # If an executor is provided by the caller (e.g. a mock in tests),
        # avoid resolving connection settings so construction remains simple
        # and predictable. Only resolve settings when we need to create
        # a real `OdooRpcExecutor`.
        if executor is not None:
            self._executor = executor
            _logger.info("Initializing OdooClient with injected executor")
        else:
            settings = OdooConnectionSettings.from_sources(
                url=url,
                db=db,
                username=username,
                password=password,
                config_path=config_path,
            )
            _logger.info("Initializing OdooClient for db=%s", settings.db)
            self._executor = OdooRpcExecutor(
                settings.url,
                settings.db,
                settings.username,
                settings.password,
            )
        self._env = OdooEnv(self._executor)
        self._models: Dict[str, OdooModel] = {}
        self._lock = threading.Lock()

    @property
    def uid(self) -> int:
        """Lazily authenticates and returns the user ID.

        :raises ConnectionError: If there is a problem connecting to the Odoo server.
        :raises PermissionError: If authentication fails for the user.
        :return: The user ID.
        :rtype: int
        """
        if not isinstance(self._executor, OdooRpcExecutor):
            raise AttributeError("Configured executor does not expose uid")
        return self._executor.uid

    @property
    def env(self) -> OdooEnv:
        """Returns the root environment for env-bound Phase A behavior."""
        return self._env

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        """Base XML-RPC execution wrapper

        :param model: The name of the Odoo model to call (e.g. 'res.partner').
        :type model: str
        :param method: The name of the method to call on the Odoo model.
        :type method: str
        :raises RuntimeError: If the Odoo server returns an error.
        :raises RuntimeError: If there is an XML-RPC communication error.
        :return: The result of the Odoo method call.
        :rtype: Any
        """
        _logger.debug("Delegating execute for %s.%s", model, method)
        return self._executor.execute(model, method, *args, **kwargs)

    def __getitem__(self, model_name: str) -> OdooModel:
        """Lazy-loads and caches the model client.

        :param model_name: The name of the Odoo model to access (e.g. 'res.partner').
        :type model_name: str
        :return: The Odoo model client.
        :rtype: OdooModel
        """
        if model_name not in self._models:
            _logger.debug("Creating model proxy for %s", model_name)
            with self._lock:
                if model_name not in self._models:
                    self._models[model_name] = OdooModel(
                        self._executor,
                        model_name,
                        env=self._env,
                    )
        return self._models[model_name]

    def __iter__(self) -> None:
        """Prevents treating the client as a container of models."""
        raise TypeError("OdooClient is not iterable")
