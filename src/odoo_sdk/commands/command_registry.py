from typing import Dict, Iterator, Tuple, Type

from odoo_sdk.client.client import OdooClient

from .command import Command


class Registry:
    """Register :class:`Command` classes that share one `OdooClient` dependency.

    The registry is necessary for consumer-side command wiring because it keeps
    use-case orchestration separate from transport details while still injecting the
    shared SDK facade into each registered command. Registered commands must
    implement the :class:`Command` Protocol (``execute`` plus ``_name`` and
    ``_description``); plain callables are not supported.

    :param client: Odoo client instance shared with all registered commands.
    :type client: OdooClient
    """

    def __init__(self, client: OdooClient):
        """Initialize the registry with its shared client dependency.

        The constructor is necessary because registered commands are created lazily,
        so the registry must retain the client that each command will receive.

        :param client: Odoo client instance shared with all registered commands.
        :type client: OdooClient
        :return: None.
        :rtype: None
        """

        self._client = client
        self._commands: Dict[str, Type[Command]] = {}

    def register(
        self,
        command_name: str,
        command: Type[Command],
    ) -> None:
        """Register a command class under a stable command name.

        This method is necessary because the registry acts as the single registry of
        available commands and ensures each command can be instantiated with the
        shared client only when it is actually requested.

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
        """Instantiate and return the command bound to the shared client.

        This lookup is necessary because consumers use dictionary-style access to
        resolve commands lazily instead of constructing every command up front.

        :param command_name: Registered command name to resolve.
        :type command_name: str
        :raises KeyError: Raised when no command is registered for the name.
        :return: Command instance bound to the shared client.
        :rtype: Command
        """

        command = self._commands[command_name]
        return command(self._client)

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
