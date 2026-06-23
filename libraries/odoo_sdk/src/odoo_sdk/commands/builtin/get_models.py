from typing import Any, Dict, List

from ..command import Command


class GetModelsCommand(Command):
    """List the available Odoo models with their technical and display names."""

    _name = "get_models"
    _description = "Get a list of all models with their names."

    def execute(self) -> List[Dict[str, Any]]:
        """Return every ``ir.model`` record's technical and display name.

        :return: A list of dictionaries with ``model`` and ``name`` keys.
        :rtype: List[Dict[str, Any]]
        """

        return self._client["ir.model"].search([]).read(["model", "name"])
