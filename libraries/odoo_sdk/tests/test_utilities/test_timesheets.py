"""Fake-executor unit tests for the read-only ``timesheet_summary`` helper.

The helper issues only ``read_group`` / ``read`` calls. These tests pin the
exact domain and ``read_group`` arguments per grouping axis, the shaped result,
empty ranges, ``only_mine`` on/off, and the invalid-input error messages.
"""

import unittest
from unittest.mock import MagicMock

from odoo_sdk.utilities.timesheets import timesheet_summary


def _client(uid: int = 7) -> MagicMock:
    client = MagicMock()
    client.uid = uid
    return client


class TestTimesheetSummaryDomain(unittest.TestCase):
    def test_only_mine_true_filters_on_employee(self):
        client = _client(uid=7)
        # get_employee_id search_read, then the read_group aggregation.
        client.execute.side_effect = [
            [{"id": 42}],
            [{"project_id": [5, "Accounting"], "unit_amount": 8.0, "__count": 3}],
        ]
        result = timesheet_summary(client, "2026-07-01", "2026-07-31")

        employee_call = client.execute.call_args_list[0]
        self.assertEqual(
            employee_call.args,
            ("hr.employee", "search_read", [("user_id", "=", 7)]),
        )
        read_group_call = client.execute.call_args_list[1]
        self.assertEqual(
            read_group_call.args,
            (
                "account.analytic.line",
                "read_group",
                [
                    ("date", ">=", "2026-07-01"),
                    ("date", "<=", "2026-07-31"),
                    ("employee_id", "=", 42),
                ],
            ),
        )
        self.assertEqual(
            read_group_call.kwargs,
            {"fields": ["unit_amount"], "groupby": ["project_id"], "lazy": False},
        )
        self.assertEqual(result["only_mine"], True)

    def test_only_mine_false_skips_employee_lookup(self):
        client = _client()
        client.execute.return_value = []
        result = timesheet_summary(
            client, "2026-07-01", "2026-07-31", only_mine=False
        )
        # A single call — the aggregation — with no employee filter in the domain.
        client.execute.assert_called_once_with(
            "account.analytic.line",
            "read_group",
            [("date", ">=", "2026-07-01"), ("date", "<=", "2026-07-31")],
            fields=["unit_amount"],
            groupby=["project_id"],
            lazy=False,
        )
        self.assertEqual(result["only_mine"], False)
        self.assertEqual(result["groups"], [])
        self.assertEqual(result["total_hours"], 0.0)


class TestTimesheetSummaryGrouping(unittest.TestCase):
    def test_group_by_project_shapes_rows(self):
        client = _client()
        client.execute.return_value = [
            {"project_id": [5, "Accounting"], "unit_amount": 8.0, "__count": 3},
            {"project_id": [6, "HR"], "unit_amount": 4.5, "__count": 2},
        ]
        result = timesheet_summary(
            client, "2026-07-01", "2026-07-31", group_by="project", only_mine=False
        )
        self.assertEqual(
            result,
            {
                "group_by": "project",
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
                "only_mine": False,
                "unit": "hours",
                "groups": [
                    {"label": "Accounting", "hours": 8.0, "entries": 3},
                    {"label": "HR", "hours": 4.5, "entries": 2},
                ],
                "total_hours": 12.5,
            },
        )

    def test_group_by_task_uses_task_field(self):
        client = _client()
        client.execute.return_value = [
            {"task_id": [10, "Fix VAT"], "unit_amount": 2.0, "__count": 1},
        ]
        result = timesheet_summary(
            client, "2026-07-01", "2026-07-31", group_by="task", only_mine=False
        )
        self.assertEqual(client.execute.call_args.kwargs["groupby"], ["task_id"])
        self.assertEqual(
            result["groups"], [{"label": "Fix VAT", "hours": 2.0, "entries": 1}]
        )

    def test_unassigned_many2one_labelled_none(self):
        client = _client()
        client.execute.return_value = [
            {"project_id": False, "unit_amount": 1.5, "__count": 1},
        ]
        result = timesheet_summary(
            client, "2026-07-01", "2026-07-31", group_by="project", only_mine=False
        )
        self.assertEqual(
            result["groups"], [{"label": None, "hours": 1.5, "entries": 1}]
        )

    def test_group_by_day_uses_iso_range_boundary(self):
        client = _client()
        client.execute.return_value = [
            {
                "date:day": "12 Jul 2026",
                "__range": {"date:day": {"from": "2026-07-12", "to": "2026-07-13"}},
                "unit_amount": 2.5,
                "__count": 2,
            },
            {
                "date:day": "13 Jul 2026",
                "__range": {"date:day": {"from": "2026-07-13", "to": "2026-07-14"}},
                "unit_amount": 1.0,
                "__count": 1,
            },
        ]
        result = timesheet_summary(
            client, "2026-07-12", "2026-07-13", group_by="day", only_mine=False
        )
        self.assertEqual(client.execute.call_args.kwargs["groupby"], ["date:day"])
        self.assertEqual(
            result["groups"],
            [
                {"label": "2026-07-12", "hours": 2.5, "entries": 2},
                {"label": "2026-07-13", "hours": 1.0, "entries": 1},
            ],
        )
        self.assertEqual(result["total_hours"], 3.5)

    def test_group_by_day_falls_back_when_range_boundary_lacks_from(self):
        # A ``__range`` entry present but without a usable ``from`` boundary must
        # fall through to the raw ``date:day`` value rather than crash.
        client = _client()
        client.execute.return_value = [
            {
                "date:day": "2026-07-12",
                "__range": {"date:day": {}},
                "unit_amount": 1.0,
                "__count": 1,
            },
        ]
        result = timesheet_summary(
            client, "2026-07-12", "2026-07-12", group_by="day", only_mine=False
        )
        self.assertEqual(
            result["groups"], [{"label": "2026-07-12", "hours": 1.0, "entries": 1}]
        )

    def test_group_by_day_falls_back_to_raw_value_without_range(self):
        client = _client()
        client.execute.return_value = [
            {"date:day": "2026-07-12", "unit_amount": 1.0, "__count": 1},
            {"date:day": False, "unit_amount": 0.5, "__count": 1},
        ]
        result = timesheet_summary(
            client, "2026-07-12", "2026-07-12", group_by="day", only_mine=False
        )
        self.assertEqual(
            result["groups"],
            [
                {"label": "2026-07-12", "hours": 1.0, "entries": 1},
                {"label": None, "hours": 0.5, "entries": 1},
            ],
        )


class TestTimesheetSummaryClientGrouping(unittest.TestCase):
    def test_resolves_partner_via_project_and_aggregates(self):
        client = _client()
        client.execute.side_effect = [
            # read_group grouped by project_id
            [
                {"project_id": [5, "Accounting"], "unit_amount": 8.0, "__count": 3},
                {"project_id": [6, "Payroll"], "unit_amount": 2.0, "__count": 1},
            ],
            # project.project read of partner_id — both projects share one client
            [
                {"id": 5, "partner_id": [99, "Globex"]},
                {"id": 6, "partner_id": [99, "Globex"]},
            ],
        ]
        result = timesheet_summary(
            client, "2026-07-01", "2026-07-31", group_by="client", only_mine=False
        )
        # The project read requests only partner_id for the unique project ids.
        project_read = client.execute.call_args_list[1]
        self.assertEqual(project_read.args, ("project.project", "read", [5, 6]))
        self.assertEqual(project_read.kwargs, {"fields": ["partner_id"]})
        # Both projects roll up under the shared partner: hours and entries summed.
        self.assertEqual(
            result["groups"], [{"label": "Globex", "hours": 10.0, "entries": 4}]
        )
        self.assertEqual(result["total_hours"], 10.0)

    def test_project_without_partner_and_row_without_project(self):
        client = _client()
        client.execute.side_effect = [
            [
                {"project_id": [5, "Internal"], "unit_amount": 3.0, "__count": 2},
                {"project_id": False, "unit_amount": 1.0, "__count": 1},
            ],
            [{"id": 5, "partner_id": False}],
        ]
        result = timesheet_summary(
            client, "2026-07-01", "2026-07-31", group_by="client", only_mine=False
        )
        # A partner-less project and a project-less row both collapse to None.
        self.assertEqual(
            result["groups"], [{"label": None, "hours": 4.0, "entries": 3}]
        )

    def test_no_rows_skips_project_read(self):
        client = _client()
        client.execute.return_value = []
        result = timesheet_summary(
            client, "2026-07-01", "2026-07-31", group_by="client", only_mine=False
        )
        # Empty aggregation: only the read_group call, no project.project read.
        client.execute.assert_called_once()
        self.assertEqual(result["groups"], [])
        self.assertEqual(result["total_hours"], 0.0)


class TestTimesheetSummaryValidation(unittest.TestCase):
    def test_invalid_group_by_raises(self):
        client = _client()
        with self.assertRaises(ValueError) as ctx:
            timesheet_summary(
                client, "2026-07-01", "2026-07-31", group_by="employee"
            )
        self.assertIn("Invalid group_by 'employee'", str(ctx.exception))
        client.execute.assert_not_called()

    def test_malformed_start_date_raises(self):
        client = _client()
        with self.assertRaises(ValueError) as ctx:
            timesheet_summary(client, "07/01/2026", "2026-07-31", only_mine=False)
        self.assertIn("Invalid start_date '07/01/2026'", str(ctx.exception))
        self.assertIn("YYYY-MM-DD", str(ctx.exception))
        client.execute.assert_not_called()

    def test_malformed_end_date_raises(self):
        client = _client()
        with self.assertRaises(ValueError) as ctx:
            timesheet_summary(client, "2026-07-01", "not-a-date", only_mine=False)
        self.assertIn("Invalid end_date 'not-a-date'", str(ctx.exception))
        client.execute.assert_not_called()

    def test_non_string_date_raises_value_error(self):
        client = _client()
        with self.assertRaises(ValueError) as ctx:
            timesheet_summary(client, None, "2026-07-31", only_mine=False)
        self.assertIn("Invalid start_date", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
