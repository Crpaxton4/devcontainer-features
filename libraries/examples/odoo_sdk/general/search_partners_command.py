from typing import Any, Dict, List, Optional, Tuple

from odoo_sdk.commands import Command


class SearchPartnersCommand(Command):
    """Search ``res.partner`` records matching an optional domain filter."""

    _name = "search_partners"
    _description = "Search partners matching a domain and return their details."

    def execute(
        self, domain: Optional[List[Tuple[str, str, Any]]] = None, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Searches for partners matching the given domain and returns their details.

        :param domain: The search domain to filter partners, defaults to None
        :type domain: Optional[List[Tuple[str, str, Any]]], optional
        :return: A list of partner records matching the search criteria.
        :rtype: List[Dict[str, Any]]
        """
        domain = domain or []

        # Business logic: We always want to fetch country and email data for partners
        fields_to_fetch = ["name", "email", "is_company", "country_id", "phone"]

        return (
            self._client["res.partner"]
            .search(
                domain,
                limit=limit,
            )
            .read(fields_to_fetch)
        )
