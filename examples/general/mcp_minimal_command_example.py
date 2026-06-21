"""Minimal example: define a custom command, register it, and serve it over MCP.

This is the smallest end-to-end demonstration of use case #2 (custom commands):

    1. Define a command that follows the ``Command`` Protocol.
    2. Register it on a ``Registry``.
    3. Start ``OdooMCPServer`` with that registry.

The command here is a trivial no-op (it just echoes its argument) so the focus
stays on the wiring rather than on Odoo behaviour - it never touches the client.
The MCP server still generates a fully typed tool schema from ``execute``'s
signature, so an agent sees a ``ping`` tool with an optional ``message`` string.

Run it (a configured Odoo connection is only needed to build the client; the
no-op command itself never connects)::

    python examples/general/mcp_minimal_command_example.py
"""

from odoo_sdk import OdooClient, Registry
from odoo_sdk.commands import Command
from odoo_sdk.mcp import OdooMCPServer


class PingCommand(Command):
    """A trivial no-op command that echoes a message back."""

    _name = "ping"
    _description = "Health-check command that echoes a message back."

    def execute(self, message: str = "ping") -> str:
        """Return ``message`` unchanged - a no-op that never touches Odoo.

        :param message: Text to echo back; defaults to ``"ping"``.
        :type message: str
        :return: The same ``message`` value.
        :rtype: str
        """
        return message


def main() -> None:
    # The registry needs a client to inject into commands; this no-op command
    # ignores it, so no Odoo connection is made when the tool is invoked.
    client = OdooClient()

    registry = Registry(client)
    registry.register("ping", PingCommand)

    OdooMCPServer(registry, server_name="Minimal Custom Command Server").run()


if __name__ == "__main__":
    main()
