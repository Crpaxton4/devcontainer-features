import threading
from typing import Any, Dict, Optional

from .odoo_config import OdooConnectionSettings
from .odoo_env import OdooEnv
from .odoo_executor import OdooExecutor
from .odoo_model import OdooModel
from .odoo_rpc_executor import OdooRpcExecutor


class OdooClient(OdooExecutor):
    """Expose the public facade for model lookup and executor-backed access.

    The client is the supported SDK entry point. It is necessary because consumers
    need one object that owns connection bootstrap, shared environment state, and
    cached model proxies while hiding the lower-level executor and transport details.

    :param url: URL of the Odoo server, defaults to None.
    :type url: Optional[str]
    :param db: Database name to authenticate against, defaults to None.
    :type db: Optional[str]
    :param username: Username used for authentication, defaults to None.
    :type username: Optional[str]
    :param password: Password or API key used for authentication, defaults to None.
    :type password: Optional[str]
    :param executor: Prebuilt executor to inject instead of creating XML-RPC
        transport state, defaults to None.
    :type executor: Optional[OdooExecutor]
    :param config_path: Optional path to an INI file with connection settings,
        defaults to None.
    :type config_path: Optional[str]
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
        """Initialize the facade with either injected or resolved connection state.

        This constructor is necessary because tests and local tooling need to inject
        executors directly, while production-style callers need configuration values
        resolved into a concrete XML-RPC executor automatically.

        :param url: URL of the Odoo server, defaults to None.
        :type url: Optional[str]
        :param db: Database name to authenticate against, defaults to None.
        :type db: Optional[str]
        :param username: Username used for authentication, defaults to None.
        :type username: Optional[str]
        :param password: Password or API key used for authentication, defaults to
            None.
        :type password: Optional[str]
        :param executor: Prebuilt executor to inject instead of creating XML-RPC
            transport state, defaults to None.
        :type executor: Optional[OdooExecutor]
        :param config_path: Optional path to an INI file with connection settings,
            defaults to None.
        :type config_path: Optional[str]
        :return: None.
        :rtype: None
        """
        # If an executor is provided by the caller (e.g. a mock in tests),
        # avoid resolving connection settings so construction remains simple
        # and predictable. Only resolve settings when we need to create
        # a real `OdooRpcExecutor`.
        if executor is not None:
            self._executor = executor
        else:
            settings = OdooConnectionSettings.from_sources(
                url=url,
                db=db,
                username=username,
                password=password,
                config_path=config_path,
            )
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
        """Return the authenticated Odoo user id.

        This property is necessary because some consumers need direct access to the
        authenticated identity while the client still controls when authentication is
        triggered.

        :return: Authenticated Odoo user identifier.
        :rtype: int
        """
        return self._executor.uid

    @property
    def env(self) -> OdooEnv:
        """Expose the root environment shared by client-created objects.

        This property is necessary so advanced callers and compatibility layers can
        anchor recordsets and derived contexts to the same metadata cache and
        executor-owned runtime state.

        :return: Root environment bound to this client.
        :rtype: OdooEnv
        """
        return self._env

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        """Delegate one model method call to the underlying executor.

        This wrapper is necessary because the public facade must satisfy the executor
        contract while still allowing the injected or constructed executor to own the
        actual transport implementation.

        :param model: Name of the Odoo model to call.
        :type model: str
        :param method: Name of the Odoo method to invoke.
        :type method: str
        :param args: Positional RPC arguments forwarded to the executor.
        :type args: Any
        :param kwargs: Keyword RPC arguments forwarded to the executor.
        :type kwargs: Any
        :return: Result returned by the executor.
        :rtype: Any
        """
        return self._executor.execute(model, method, *args, **kwargs)

    def __getitem__(self, model_name: str) -> OdooModel:
        """Return a cached model proxy for one Odoo model name.

        This lookup is necessary because the facade acts like a model registry for
        consumers, and reusing model proxies keeps shared environment state stable
        across repeated accesses.

        :param model_name: Name of the Odoo model to access.
        :type model_name: str
        :return: Cached or newly created model proxy.
        :rtype: OdooModel
        """
        if model_name not in self._models:
            with self._lock:
                if model_name not in self._models:
                    self._models[model_name] = OdooModel(
                        self._executor,
                        model_name,
                        env=self._env,
                    )
        return self._models[model_name]

    def __iter__(self) -> None:
        """Reject iteration over the client facade.

        This guard is necessary because the client behaves like keyed model access,
        not a materialized collection of every model exposed by the server.

        :raises TypeError: Always raised to prevent accidental iteration.
        :return: This method never returns successfully.
        :rtype: None
        """
        raise TypeError("OdooClient is not iterable")
