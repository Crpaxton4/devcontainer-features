from abc import ABC, abstractmethod
from typing import Any, Optional

from odoo_sdk.client import OdooClient
from odoo_sdk.state import LocalConfig, LocalStateClient


class Command(ABC):
    """Base interface for all Odoo SDK Commands.

    A command is an atomic, composable unit of business logic with a single
    ``execute`` entry point. Commands never reference interaction surfaces (MCP,
    CLI) and never reference each other; shared logic lives in ``utilities``.

    Commands receive three peer dependencies, injected by the :class:`Registry`:

    * ``client`` — the :class:`OdooClient` (Odoo API abstraction).
    * ``state`` — the :class:`LocalStateClient` (SQLite session FSM).
    * ``config`` — the :class:`LocalConfig` (resolved SDK settings).

    The state and config dependencies are created lazily on first access so that
    lightweight commands (and unit tests) that only need the client are not
    forced to construct SQLite state or read a config file.
    """

    _name: str
    _description: str
    _client: OdooClient

    def __init__(
        self,
        client: Optional[OdooClient] = None,
        state: Optional[LocalStateClient] = None,
        config: Optional[LocalConfig] = None,
    ):
        self._client = client if client is not None else OdooClient()
        self._injected_state = state
        self._injected_config = config

    @property
    def state(self) -> LocalStateClient:
        """Return the injected local state client, creating one on first use."""
        if self._injected_state is None:
            self._injected_state = LocalStateClient()
        return self._injected_state

    @property
    def config(self) -> LocalConfig:
        """Return the injected local config, resolving one on first use."""
        if self._injected_config is None:
            self._injected_config = LocalConfig.load()
        return self._injected_config

    @abstractmethod
    def execute(self, *args: Any, **kwargs: Any) -> Any: ...

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description
