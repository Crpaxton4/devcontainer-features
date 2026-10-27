import threading
from typing import Any, Callable, Dict, Optional

from odoo_client.records.recordset import OdooRecordset
from odoo_client.settings import OdooConnectionSettings
from odoo_client.transport.errors import OdooAuthenticationError
from odoo_client.transport.executor import OdooExecutor, guarded_execute
from odoo_client.transport.json2 import OdooJson2Executor
from odoo_client.transport.rpc import OdooRpcExecutor


class OdooClient:
    """Public facade for model lookup and executor-backed access — the SDK entry point.

    Owns shared environment state and cached model proxies while hiding the
    executor and transport details. Pass an ``executor`` directly (e.g. a test
    double), or resolved ``settings`` from which the transport executor is built.
    :func:`odoo_client.bootstrap.bootstrap` is the one place that loads the
    configuration and builds the production client.
    """

    def __init__(
        self,
        executor: Optional[OdooExecutor] = None,
        *,
        settings: Optional[OdooConnectionSettings] = None,
        credentials_refresh: Optional[Callable[[], OdooConnectionSettings]] = None,
    ):
        """Bind an injected executor, or build one from ``settings``.

        ``credentials_refresh`` lets the executor re-resolve settings and retry
        once after an authentication failure (a key rotation).

        :raises ValueError: When neither ``executor`` nor ``settings`` is given.
        """
        if executor is not None:
            self._executor = executor
        elif settings is not None:
            self._executor = self._build_executor(settings, credentials_refresh)
        else:
            raise ValueError("OdooClient requires an executor or settings")
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
    def from_settings(
        cls,
        settings: OdooConnectionSettings,
        *,
        credentials_refresh: Optional[Callable[[], OdooConnectionSettings]] = None,
    ) -> "OdooClient":
        """Create a client whose executor is built from resolved ``settings``."""
        return cls(settings=settings, credentials_refresh=credentials_refresh)

    @staticmethod
    def _build_executor(
        settings: OdooConnectionSettings,
        credentials_refresh: Optional[Callable[[], OdooConnectionSettings]],
    ) -> OdooExecutor:
        """Return the transport executor ``settings`` selects."""
        if settings.transport == "json2":
            return OdooJson2Executor(
                settings.url,
                settings.db,
                settings.api_key,  # type: ignore[arg-type]
                timeout=settings.timeout,
                credentials_refresh=credentials_refresh,
            )
        return OdooRpcExecutor(
            settings.url,
            settings.db,
            settings.username,  # type: ignore[arg-type]
            settings.password,  # type: ignore[arg-type]
            timeout=settings.timeout,
            credentials_refresh=credentials_refresh,
        )

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

        Satisfies the :class:`~odoo_client.commands.protocols.RpcClient` contract by
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
