from typing import Any, Optional, Protocol

from odoo_sdk.client import OdooClient


class Command(Protocol):
    """
    Base interface for all Odoo SDK Commands.
    Ensures commands are expressive and compatible with dynamic tool generation.
    """

    _name: str
    _description: str
    _client: OdooClient

    def __init__(self, client: Optional[OdooClient] = None):
        """Initialize the command with its required dependencies.

        The constructor is necessary because all commands require access to the shared
        Odoo client, and this ensures that dependency is injected at instantiation.

        :param client: Odoo client instance shared across commands.
        :type client: Any
        :return: None.
        :rtype: None
        """
        self._client = client if client is not None else OdooClient()

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        """Executes the command logic. Signature defines the tool arguments.

        :return: Result of command execution, can be any type depending on the command's purpose.
        :rtype: Any
        """
        pass

    @property
    def name(self) -> str:
        """Public name of the command, used for registration and lookup.

        :return: Name string.
        :rtype: str
        """
        return self._name

    @property
    def description(self) -> str:
        """Human-readable description of the command's purpose and usage.

        :return: Description string.
        :rtype: str
        """
        return self._description
