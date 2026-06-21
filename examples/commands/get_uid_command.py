"""Example command following the SDK ``Command`` Protocol.

Commands set ``_name``/``_description`` and implement ``execute`` with a typed
signature. The MCP server uses that signature to generate a tool's input schema.
"""

from odoo_sdk.commands import Command


class GetUidCommand(Command):
    """Return the UID of the authenticated Odoo user."""

    _name = "get_uid"
    _description = "Get the UID of the current user."

    def execute(self) -> int:
        """Get the UID of the current user."""
        return self._client.uid
