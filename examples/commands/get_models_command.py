from typing import Any, Dict, List

from odoo_sdk.commands import Command


class GetModelsCommand(Command):
    """List the available Odoo models with their names."""

    _name = "get_models"
    _description = "Get a list of all models with their names."

    def execute(self) -> List[Dict[str, Any]]:
        """Get a list of all models with their names.

        :return: A list of dictionaries containing model information.
        :rtype: List[Dict[str, Any]]
        """
        return self._client["ir.model"].search([]).read(["model", "name"])
