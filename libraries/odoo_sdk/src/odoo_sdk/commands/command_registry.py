from typing import Dict, Iterator, Optional, Tuple, Type

from odoo_sdk.state import LocalConfig, LocalStateClient

from .command import Command
from .protocols import RpcClient


class Registry:
    """Register :class:`Command` classes that share their peer dependencies.

    The registry keeps use-case orchestration separate from transport and state
    details while injecting the shared SDK dependencies into each registered
    command. Commands receive three peers: the :class:`RpcClient`, the
    :class:`LocalStateClient` (SQLite session FSM), and the :class:`LocalConfig`
    (resolved SDK settings). The state client and config are optional; when
    omitted, commands resolve their own lazily on first use, preserving the
    original single-dependency behavior.

    :param client: RPC client instance shared with all registered commands.
    :type client: RpcClient
    :param state_client: Shared local state client, defaults to None.
    :type state_client: Optional[LocalStateClient]
    :param config: Shared resolved SDK configuration, defaults to None.
    :type config: Optional[LocalConfig]
    """

    def __init__(
        self,
        client: RpcClient,
        state_client: Optional[LocalStateClient] = None,
        config: Optional[LocalConfig] = None,
    ):
        """Initialize the registry with its shared dependencies.

        The constructor retains the client (and, optionally, the local state
        client and config) that each command will receive when it is created
        lazily on lookup.

        :param client: RPC client instance shared with all registered commands.
        :type client: RpcClient
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

        For :class:`Command` subclasses, the shared client, state client, and
        config are passed as constructor arguments; :meth:`Command.__init__`
        stores each peer and lazily resolves its own default when the registry
        passes ``None``, so omitting the optional state client or config is
        behavior-preserving. Classes that merely satisfy the ``Command`` Protocol
        (whose ``__init__`` accepts only the client) receive the client alone.

        :param command_name: Registered command name to resolve.
        :type command_name: str
        :raises KeyError: Raised when no command is registered for the name.
        :return: Command instance bound to the shared dependencies.
        :rtype: Command
        """

        command_cls = self._commands[command_name]
        if issubclass(command_cls, Command):
            return command_cls(
                self._client,
                state=self._state_client,
                config=self._config,
            )
        return command_cls(self._client)

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
