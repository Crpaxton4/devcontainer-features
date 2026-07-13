import threading
from typing import TYPE_CHECKING, Any, Dict, Optional

from odoo_sdk.records.recordset import OdooRecordset
from odoo_sdk.state.config import OdooConnectionSettings
from odoo_sdk.transport.executor import OdooExecutor, guarded_execute
from odoo_sdk.transport.json2 import OdooJson2Executor
from odoo_sdk.transport.rpc import OdooRpcExecutor

if TYPE_CHECKING:  # pragma: no cover
    from odoo_sdk.state.config import LocalConfig


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
        config: Optional["LocalConfig"] = None,
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
            if config is not None:
                settings = config.connection_settings()
            else:
                settings = OdooConnectionSettings.from_sources(
                    url=url,
                    db=db,
                    username=username,
                    password=password,
                    config_path=config_path,
                )
            if settings.transport == "json2":
                self._executor = OdooJson2Executor(
                    settings.url,
                    settings.db,
                    settings.api_key,  # type: ignore[arg-type]
                    timeout=settings.timeout,
                )
            else:
                self._executor = OdooRpcExecutor(
                    settings.url,
                    settings.db,
                    settings.username,  # type: ignore[arg-type]
                    settings.password,  # type: ignore[arg-type]
                    timeout=settings.timeout,
                )
        self._root_recordset = OdooRecordset(
            executor=self._executor,
            model_name="",
            ids=(),
            context={},
        )
        self._model_recordsets: Dict[str, OdooRecordset] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_xml_rpc(
        cls,
        url: str,
        db: str,
        username: str,
        password: str,
    ) -> "OdooClient":
        """Create a client backed by an XML-RPC executor.

        :param url: Base URL of the Odoo server.
        :param db: Database name to authenticate against.
        :param username: Username used for authentication.
        :param password: Password used for authentication.
        :return: OdooClient backed by OdooRpcExecutor.
        :rtype: OdooClient
        """
        return cls(executor=OdooRpcExecutor(url, db, username, password))

    @classmethod
    def from_json2(
        cls,
        url: str,
        db: str,
        api_key: str,
    ) -> "OdooClient":
        """Create a client backed by a JSON-2 executor.

        :param url: Base URL of the Odoo server.
        :param db: Database name to authenticate against.
        :param api_key: Bearer API key used for authentication.
        :return: OdooClient backed by OdooJson2Executor.
        :rtype: OdooClient
        """
        return cls(executor=OdooJson2Executor(url, db, api_key))

    @classmethod
    def from_config(cls, config: "LocalConfig") -> "OdooClient":
        """Create a client whose executor is built from an injected LocalConfig.

        This factory realizes the layered design where connection settings are
        resolved once (File > Env > Default) by :class:`LocalConfig` and injected,
        rather than each client resolving settings internally.

        :param config: Resolved local configuration.
        :type config: LocalConfig
        :return: OdooClient backed by the transport chosen by ``config``.
        :rtype: OdooClient
        """
        return cls(config=config)

    @property
    def uid(self) -> int:
        """Return the authenticated Odoo user id.

        This property is necessary because some consumers need direct access to the
        authenticated identity while the client still controls when authentication is
        triggered.

        :return: Authenticated Odoo user identifier.
        :rtype: int
        """
        return int(self._executor.uid)

    @property
    def authenticated(self) -> bool:
        """Indicate whether the client has successfully authenticated.

        This property is necessary because some consumers need a simple boolean check
        for authentication status without directly accessing the uid or handling
        exceptions from failed authentication attempts.

        :return: True if authenticated successfully, False otherwise.
        :rtype: bool
        """
        return bool(self._executor.uid)

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        """Delegate one model method call through the shared guarded seam.

        This wrapper is necessary because the public facade must satisfy the executor
        contract while still allowing the injected or constructed executor to own the
        actual transport implementation. It routes through
        :func:`guarded_execute` — the single chokepoint that applies the
        cross-cutting ``forbid_unlink`` guard exactly once.

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
        return guarded_execute(self._executor, model, method, *args, **kwargs)

    def __getitem__(self, model_name: str) -> OdooRecordset:
        """Return a cached model-bound recordset for one Odoo model name.

        This lookup is necessary because the client acts like Odoo's model registry,
        and the supported high-level contract starts from empty model-bound recordsets.

        :param model_name: Name of the Odoo model to access.
        :type model_name: str
        :return: Cached or newly created empty recordset bound to the model.
        :rtype: OdooRecordset
        """
        if model_name not in self._model_recordsets:
            with self._lock:
                if model_name not in self._model_recordsets:
                    self._model_recordsets[model_name] = self._root_recordset.recordset(
                        model_name
                    )
        return self._model_recordsets[model_name]

    def __iter__(self) -> None:
        """Reject iteration over the client facade.

        This guard is necessary because the client behaves like keyed model access,
        not a materialized collection of every model exposed by the server.

        :raises TypeError: Always raised to prevent accidental iteration.
        :return: This method never returns successfully.
        :rtype: None
        """
        raise TypeError("OdooClient is not iterable")
