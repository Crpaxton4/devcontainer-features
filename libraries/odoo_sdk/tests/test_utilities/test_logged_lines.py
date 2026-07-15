"""Tests for the read-only already-logged timesheet-hours fetch (#378 item 7).

The review surface uses this to warn when a derived session's task/day already
carries hand-entered Odoo lines. These drive the pure aggregation over a fake
transport (no live Odoo) and assert the exact ``(task_id, day) -> hours`` map and
the read-only calls issued.
"""

import unittest

from odoo_sdk.utilities.logged_lines import logged_hours_by_task_day


class FakeClient:
    """A recording read-only transport: canned rows, captured execute calls."""

    def __init__(self, rows, uid=7, employee_id=42):
        self._rows = rows
        self.uid = uid
        self._employee_id = employee_id
        self.calls = []

    def execute(self, model, method, *args, **kwargs):
        self.calls.append((model, method, args, kwargs))
        if model == "hr.employee":
            return [{"id": self._employee_id}]
        return self._rows


def _line(task_id, date, hours):
    return {"task_id": [task_id, f"Task {task_id}"], "date": date, "unit_amount": hours}


class TestLoggedHoursByTaskDay(unittest.TestCase):
    def test_aggregates_by_task_and_day(self):
        client = FakeClient(
            [
                _line(24648, "2026-07-01", 1.5),
                _line(24648, "2026-07-01", 0.5),
                _line(24648, "2026-07-02", 2.0),
                _line(31000, "2026-07-01", 3.0),
            ]
        )
        result = logged_hours_by_task_day(
            client, [24648, 31000], "2026-07-01", "2026-07-02"
        )
        self.assertEqual(
            result,
            {
                ("24648", "2026-07-01"): 2.0,
                ("24648", "2026-07-02"): 2.0,
                ("31000", "2026-07-01"): 3.0,
            },
        )

    def test_only_mine_filters_by_employee_and_reads_only(self):
        client = FakeClient([_line(24648, "2026-07-01", 1.0)])
        logged_hours_by_task_day(client, [24648], "2026-07-01", "2026-07-15")
        models_methods = [(m, meth) for m, meth, _, _ in client.calls]
        self.assertIn(("hr.employee", "search_read"), models_methods)
        line_call = next(c for c in client.calls if c[0] == "account.analytic.line")
        self.assertEqual(line_call[1], "search_read")
        domain = line_call[2][0]
        self.assertIn(("employee_id", "=", 42), domain)
        self.assertIn(("task_id", "in", [24648]), domain)
        self.assertIn(("date", ">=", "2026-07-01"), domain)
        self.assertIn(("date", "<=", "2026-07-15"), domain)

    def test_only_mine_false_skips_employee_lookup(self):
        client = FakeClient([_line(24648, "2026-07-01", 1.0)])
        logged_hours_by_task_day(
            client, [24648], "2026-07-01", "2026-07-15", only_mine=False
        )
        self.assertNotIn(
            "hr.employee", [model for model, _, _, _ in client.calls]
        )

    def test_non_numeric_task_ids_yield_empty_without_querying(self):
        client = FakeClient([])
        result = logged_hours_by_task_day(client, ["", "abc"], "2026-07-01", "2026-07-02")
        self.assertEqual(result, {})
        self.assertEqual(client.calls, [])

    def test_rows_missing_task_or_date_are_skipped(self):
        client = FakeClient(
            [
                {"task_id": False, "date": "2026-07-01", "unit_amount": 1.0},
                {"task_id": [24648, "T"], "date": "", "unit_amount": 1.0},
                _line(24648, "2026-07-01", 0.25),
            ]
        )
        result = logged_hours_by_task_day(client, [24648], "2026-07-01", "2026-07-02")
        self.assertEqual(result, {("24648", "2026-07-01"): 0.25})


if __name__ == "__main__":
    unittest.main()
