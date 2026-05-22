from typing import Any, Dict, Callable

from ..odoo_service import OdooClient


class CommandDispatcher:
    """
    Command Rigistry that registerd commands holds the shared resources that are injected into the command handlers.
    """

    def __init__(self, client: OdooClient):
        self._client = client
        # _commands maps a command name to a factory that accepts the shared
        # `OdooClient` and returns a callable (typically a command instance
        # whose `__call__` runs the command).
        self._commands: Dict[str, Callable[[OdooClient], Callable[..., Any]]] = {}

    def register(
        self,
        command_name: str,
        command_factory: Callable[[OdooClient], Callable[..., Any]],
    ) -> None:
        """Register a command factory or class with a command name.

        The registered value should be a callable that accepts a single
        `OdooClient` and returns a callable object that can be invoked.
        This keeps the runtime API flexible while allowing classes to be
        registered directly (classes are callables that return instances).
        """
        self._commands[command_name] = command_factory

    def __getitem__(self, command_name: str) -> Callable[..., Any]:
        """Return a callable for the registered command name.

        Usage: `dispatcher["echo"]("hello")` — the returned value is the
        command callable bound to the shared client.

        Raises `KeyError` if the command is not registered.
        """
        command_factory = self._commands.get(command_name)
        if not command_factory:
            raise KeyError(command_name)
        return command_factory(self._client)
