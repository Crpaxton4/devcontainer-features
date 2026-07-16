import threading
from typing import TYPE_CHECKING, Any, Dict, Optional

from odoo_sdk.records.recordset import OdooRecordset
from odoo_sdk.state.config import OdooConnectionSettings
from odoo_sdk.transport.errors import OdooAuthenticationError
from odoo_sdk.transport.executor import OdooExecutor, guarded_execute
from odoo_sdk.transport.json2 import OdooJson2Executor
from odoo_sdk.transport.rpc import OdooRpcExecutor

if TYPE_CHECKING:  # pragma: no cover
    from odoo_sdk.state.config import LocalConfig


class OdooClient:
    """Public facade for model lookup and executor-backed access — the SDK entry point.

    Owns connection bootstrap, shared environment state, and cached model proxies
    while hiding the executor and transport details. An ``executor`` may be injected
    directly (e.g. a test double); otherwise connection state is resolved from an
    explicit ``config``, then from ``url``/``db``/``username``/``password`` and
    ``config_path`` via :meth:`OdooConnectionSettings.from_sources`.
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
        """Bind an injected executor, or resolve one from ``config``/args."""
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
        """Create a client backed by an XML-RPC executor."""
        return cls(executor=OdooRpcExecutor(url, db, username, password))

    @classmethod
    def from_json2(
        cls,
        url: str,
        db: str,
        api_key: str,
    ) -> "OdooClient":
        """Create a client backed by a JSON-2 executor."""
        return cls(executor=OdooJson2Executor(url, db, api_key))

    @classmethod
    def from_config(cls, config: "LocalConfig") -> "OdooClient":
        """Create a client whose executor is built from an injected LocalConfig.

        This factory realizes the layered design where connection settings are
        resolved once (File > Env > Default) by :class:`LocalConfig` and injected,
        rather than each client resolving settings internally.
        """
        return cls(config=config)

    @property
    def uid(self) -> int:
        """Return the authenticated Odoo user id."""
        return int(self._executor.uid)

    @property
    def authenticated(self) -> bool:
        """Return whether the client has authenticated.

        A rejected login raises :class:`OdooAuthenticationError` from the executor,
        which is translated here into ``False`` rather than propagating.
        """
        try:
            return bool(self._executor.uid)
        except OdooAuthenticationError:
            return False

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        """Delegate one model method call through the shared guarded seam.

        Satisfies the :class:`~odoo_sdk.commands.protocols.RpcClient` contract by
        composition and routes through :func:`guarded_execute` — the single
        chokepoint that applies the ``forbid_unlink`` guard exactly once.
        """
        return guarded_execute(self._executor, model, method, *args, **kwargs)

    def __getitem__(self, model_name: str) -> OdooRecordset:
        """Return a cached empty recordset bound to ``model_name``."""
        if model_name not in self._model_recordsets:
            with self._lock:
                if model_name not in self._model_recordsets:
                    self._model_recordsets[model_name] = self._root_recordset.recordset(
                        model_name
                    )
        return self._model_recordsets[model_name]

    def __iter__(self) -> None:
        """Reject iteration: the client is keyed model access, not a collection.

        :raises TypeError: Always, to prevent accidental iteration.
        """
        raise TypeError("OdooClient is not iterable")
