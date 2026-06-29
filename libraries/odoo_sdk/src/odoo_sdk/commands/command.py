from abc import ABC, abstractmethod
from typing import Any, Optional

from odoo_sdk.client import OdooClient


class Command(ABC):
    """Base interface for all Odoo SDK Commands."""

    _name: str
    _description: str
    _client: OdooClient

    def __init__(self, client: Optional[OdooClient] = None):
        self._client = client if client is not None else OdooClient()

    @abstractmethod
    def execute(self, *args: Any, **kwargs: Any) -> Any: ...

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description
