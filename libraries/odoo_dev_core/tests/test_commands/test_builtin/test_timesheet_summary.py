"""Tests for the ``timesheet_summary`` built-in command.

The command is a thin delegator to ``billing.timesheet_reports.timesheet_summary``.
"""

import unittest
from unittest.mock import MagicMock, patch

from odoo_dev_core import default_registry
from ...fakes import service

_HELPER = "odoo_dev_core.builtins.timesheet_summary.timesheet_summary"


class TestTimesheetSummaryCommand(unittest.TestCase):
    def test_registered_under_its_name(self):
        self.assertEqual(
            default_registry().get("timesheet_summary").id, "timesheet_summary"
        )

    def test_delegates_to_helper_with_all_arguments(self):
        client = MagicMock()
        with patch(_HELPER, return_value={"ok": True}) as helper:
            result = service(client=client).execute(
                "timesheet_summary",
                start_date="2026-07-01",
                end_date="2026-07-31",
                group_by="client",
                only_mine=False,
            )
        helper.assert_called_once_with(
            client,
            "2026-07-01",
            "2026-07-31",
            group_by="client",
            only_mine=False,
        )
        self.assertEqual(result, {"ok": True})

    def test_defaults_group_by_project_and_only_mine_true(self):
        client = MagicMock()
        with patch(_HELPER, return_value={}) as helper:
            service(client=client).execute(
                "timesheet_summary", start_date="2026-07-01", end_date="2026-07-31"
            )
        self.assertEqual(
            helper.call_args.kwargs, {"group_by": "project", "only_mine": True}
        )


if __name__ == "__main__":
    unittest.main()
