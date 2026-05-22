from typing import Any, Dict, List


class GetModelsCommand:
    def __init__(self, client):
        self.client = client

    def __call__(self) -> List[Dict[str, Any]]:
        """Get a list of all models with their names.

        :return: A list of dictionaries containing model information.
        :rtype: List[Dict[str, Any]]
        """
        return self.client["ir.model"].search([]).read(["model", "name"])
