from typing import Dict, Iterator, Optional, Tuple, Type

from odoo_sdk.client.client import OdooClient
from odoo_sdk.state import LocalConfig, LocalStateClient

from .command import Command


class Registry:
    """Register :class:`Command` classes that share their peer dependencies.

    The registry keeps use-case orchestration separate from transport and state
    details while injecting the shared SDK dependencies into each registered
    command. Commands receive three peers: the :class:`OdooClient`, the
    :class:`LocalStateClient` (SQLite session FSM), and the :class:`LocalConfig`
    (resolved SDK settings). The state client and config are optional; when
    omitted, commands resolve their own lazily on first use, preserving the
    original single-dependency behavior.

    :param client: Odoo client instance shared with all registered commands.
    :type client: OdooClient
    :param state_client: Shared local state client, defaults to None.
    :type state_client: Optional[LocalStateClient]
    :param config: Shared resolved SDK configuration, defaults to None.
    :type config: Optional[LocalConfig]
    """

    def __init__(
        self,
        client: OdooClient,
        state_client: Optional[LocalStateClient] = None,
        config: Optional[LocalConfig] = None,
    ):
        """Initialize the registry with its shared dependencies.

        The constructor retains the client (and, optionally, the local state
        client and config) that each command will receive when it is created
        lazily on lookup.

        :param client: Odoo client instance shared with all registered commands.
        :type client: OdooClient
        :param state_client: Shared local state client, defaults to None.
        :type state_client: Optional[LocalStateClient]
        :param config: Shared resolved SDK configuration, defaults to None.
        :type config: Optional[LocalConfig]
        :return: None.
        :rtype: None
        """

        self._client = client
        self._state_client = state_client
        self._config = config
        self._commands: Dict[str, Type[Command]] = {}

    def register(
        self,
        command_name: str,
        command: Type[Command],
    ) -> None:
        """Register a command class under a stable command name.

        This method is necessary because the registry acts as the single registry of
        available commands and ensures each command can be instantiated with the
        shared dependencies only when it is actually requested.

        :param command_name: Public name used to retrieve the command.
        :type command_name: str
        :param command: Command class to register; must implement the
            :class:`Command` Protocol.
        :type command: Type[Command]
        :return: None.
        :rtype: None
        """

        self._commands[command_name] = command

    def __getitem__(self, command_name: str) -> Command:
        """Instantiate and return the command bound to the shared dependencies.

        The command is constructed with the shared client positionally (preserving
        the original contract). When the command is a :class:`Command` subclass and
        the registry holds a state client or config, those peers are injected onto
        the instance so commands do not resolve their own.

        :param command_name: Registered command name to resolve.
        :type command_name: str
        :raises KeyError: Raised when no command is registered for the name.
        :return: Command instance bound to the shared dependencies.
        :rtype: Command
        """

        command_cls = self._commands[command_name]
        command = command_cls(self._client)
        if isinstance(command, Command):
            if self._state_client is not None:
                command._injected_state = self._state_client
            if self._config is not None:
                command._injected_config = self._config
        return command

    def __iter__(self) -> Iterator[Type[Command]]:
        """Allows iteration over registered command classes."""
        return iter(self._commands.values())

    def items(self) -> Iterator[Tuple[str, Command]]:
        """Yield ``(name, command)`` pairs, each command bound to the client.

        This accessor is necessary because dynamic consumers (such as the MCP
        server) need the public registration name alongside an instantiated
        command, which plain iteration over the stored classes does not provide.

        :return: Iterator of registration name and bound command instance pairs.
        :rtype: Iterator[Tuple[str, Command]]
        """

        for command_name in self._commands:
            yield command_name, self[command_name]
