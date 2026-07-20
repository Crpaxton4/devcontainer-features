from ..command import Command
from ._registration import builtin_command


@builtin_command
class GetUidCommand(Command):
    """Return the UID of the authenticated Odoo user."""

    _name = "get_uid"
    _description = "Get the UID of the current user."

    def execute(self) -> int:
        """Return the authenticated user's Odoo id."""
        return self._client.uid
