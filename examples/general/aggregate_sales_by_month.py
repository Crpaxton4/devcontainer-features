from typing import Any, Dict, List, Optional

from odoo_sdk.commands import Command


class AggregateSalesByMonthCommand(Command):
    """Group sale orders by month and sum the total amount per month."""

    _name = "aggregate_sales_by_month"
    _description = "Group sale orders by month and sum the total amount per month."

    def execute(
        self,
        domain: Optional[List] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return monthly sales totals for orders matching the given domain.

        :param domain: Domain used to filter sale orders, defaults to None.
        :type domain: Optional[List]
        :param limit: Maximum number of month groups to return, defaults to None.
        :type limit: Optional[int]
        :return: List of dicts with ``month`` and ``total`` keys.
        :rtype: List[Dict[str, Any]]
        """
        rows = self._client["sale.order"]._read_group(
            domain=domain,
            groupby=("date_order:month",),
            aggregates=("amount_total:sum",),
            order="date_order:month asc",
            limit=limit,
        )
        return [
            {"month": date_order_month, "total": amount_total_sum}
            for date_order_month, amount_total_sum in rows
        ]
