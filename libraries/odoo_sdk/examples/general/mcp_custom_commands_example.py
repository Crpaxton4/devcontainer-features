"""Example: running the MCP server with user-defined commands (use case #2).

The packaged server (``odoo-mcp`` / ``python -m odoo_sdk.mcp``) only exposes the
SDK's built-in commands. To expose your own commands, build a :class:`Registry`,
register your :class:`Command` implementations, and start :class:`OdooMCPServer`
yourself - which is what this script demonstrates.

A command is any class that follows the ``Command`` Protocol: set ``_name`` and
``_description``, and implement ``execute`` with a typed signature. The typed
signature becomes the tool's input schema; the docstring enriches it.

You can mix in the built-ins too via
``from odoo_sdk.commands.builtin import register_builtins``.

Run with a configured Odoo connection (env vars or ``.odoo_sdk.ini``)::

    python examples/general/mcp_custom_commands_example.py
"""

from typing import Any, Dict, List

from odoo_sdk import OdooClient, Registry
from odoo_sdk.commands import Command
from odoo_sdk.mcp import OdooMCPServer


class SearchPartnersCommand(Command):
    """Search ``res.partner`` records by a name fragment."""

    _name = "search_partners"
    _description = "Search contacts whose name matches a fragment."

    def execute(self, name: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Return partners whose ``name`` contains ``name``.

        :param name: Case-insensitive fragment to match against partner names.
        :param limit: Maximum number of partners to return.
        :return: Matching partner records with ``name`` and ``email`` fields.
        """
        return (
            self._client["res.partner"]
            .search([("name", "ilike", name)], limit=limit)
            .read(["name", "email"])
        )


def main() -> None:
    # Bootstraps the client from env vars / .odoo_sdk.ini (see OdooClient docs).
    client = OdooClient()

    registry = Registry(client)
    registry.register("search_partners", SearchPartnersCommand)
    # Optionally also expose the built-ins:
    # from odoo_sdk.commands.builtin import register_builtins
    # register_builtins(registry)

    OdooMCPServer(registry, server_name="My Odoo MCP Server").run()


if __name__ == "__main__":
    main()
