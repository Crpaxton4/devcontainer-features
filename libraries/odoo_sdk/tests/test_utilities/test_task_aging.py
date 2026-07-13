"""Fake-executor tests for the read-only ``get_task_aging`` helper.

Every helper call runs through a real ``OdooClient`` wrapping a fake
``OdooExecutor`` so the system-wide ``forbid_unlink`` guard is exercised and the
exact ``search_read`` arguments (domain / fields / order / limit) can be
asserted. Day counts are made deterministic by injecting a fixed ``now``.
"""

import unittest
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

from odoo_sdk.client import OdooClient
from odoo_sdk.commands.builtin.task_aging import TaskAgingCommand
from odoo_sdk.transport.executor import OdooExecutor
from odoo_sdk.utilities.odoo_helpers import get_task_aging

# Fixed reference "now" for deterministic day arithmetic.
_NOW = datetime(2026, 7, 12, tzinfo=timezone.utc)


def _row(task_id: int, create: str, last_stage: Any, **extra: Any) -> dict:
    """Build a raw ``project.task`` search_read row."""
    row = {
        "id": task_id,
        "name": f"Task {task_id}",
        "project_id": [7, "Consulting"],
        "stage_id": [3, "In Progress"],
        "create_date": create,
        "date_last_stage_update": last_stage,
    }
    row.update(extra)
    return row


class _AgingExecutor(OdooExecutor):
    """Fake executor that services one ``project.task`` ``search_read`` call.

    Records the positional domain and keyword options of the single expected
    call so the caller-built query can be asserted, and returns the configured
    rows unchanged (the helper does its own sorting/limiting semantics).
    """

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.recorded: dict[str, Any] = {}

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        if (model, method) != ("project.task", "search_read"):
            raise AssertionError(f"unexpected call: {model}.{method}")
        (domain,) = args
        self.recorded = {"domain": domain, **kwargs}
        return self._rows


def _run(rows: list[dict], now: Any = _NOW, **kwargs: Any):
    """Run ``get_task_aging`` against a fake executor; return (result, executor)."""
    executor = _AgingExecutor(rows)
    client = OdooClient(executor=executor)
    result = get_task_aging(client, now=now, **kwargs)
    return result, executor


class TestTaskAgingQuery(unittest.TestCase):
    """The Odoo ``search_read`` query is built exactly as specified."""

    def test_base_domain_fields_and_order(self):
        _, executor = _run([])
        self.assertEqual(
            executor.recorded["domain"], [("stage_id.fold", "=", False)]
        )
        self.assertEqual(
            executor.recorded["fields"],
            [
                "id",
                "name",
                "project_id",
                "stage_id",
                "create_date",
                "date_last_stage_update",
            ],
        )
        self.assertEqual(
            executor.recorded["order"], "date_last_stage_update asc"
        )

    def test_default_limit_is_twenty(self):
        _, executor = _run([])
        self.assertEqual(executor.recorded["limit"], 20)

    def test_custom_limit_forwarded(self):
        _, executor = _run([], limit=5)
        self.assertEqual(executor.recorded["limit"], 5)

    def test_project_filter_adds_exact_domain_term(self):
        _, executor = _run([], project_id=42)
        self.assertIn(("project_id", "=", 42), executor.recorded["domain"])

    def test_stage_filter_is_case_insensitive_name_ilike(self):
        _, executor = _run([], stage="review")
        self.assertIn(
            ("stage_id.name", "ilike", "review"), executor.recorded["domain"]
        )

    def test_empty_stage_string_adds_no_term(self):
        # A falsy ``stage`` must not append a domain term.
        _, executor = _run([], stage="")
        self.assertEqual(
            executor.recorded["domain"], [("stage_id.fold", "=", False)]
        )

    def test_project_and_stage_combine(self):
        _, executor = _run([], project_id=9, stage="qa")
        self.assertEqual(
            executor.recorded["domain"],
            [
                ("stage_id.fold", "=", False),
                ("project_id", "=", 9),
                ("stage_id.name", "ilike", "qa"),
            ],
        )


class TestTaskAgingResult(unittest.TestCase):
    """Day computation, record shape, and stalest-first ordering."""

    def test_empty_result_is_empty_list(self):
        result, _ = _run([])
        self.assertEqual(result, [])

    def test_record_shape_and_day_counts(self):
        rows = [_row(1, "2026-07-01 00:00:00", "2026-07-10 00:00:00")]
        result, _ = _run(rows)
        self.assertEqual(
            result,
            [
                {
                    "task_id": 1,
                    "name": "Task 1",
                    "project": "Consulting",
                    "stage": "In Progress",
                    "days_open": 11,
                    "days_in_stage": 2,
                }
            ],
        )

    def test_sorted_stalest_first_by_days_in_stage(self):
        # days_in_stage: A=1, B=22, C=11, D(False->create)=7.
        rows = [
            _row(1, "2026-06-01 00:00:00", "2026-07-11 00:00:00"),
            _row(2, "2026-07-01 00:00:00", "2026-06-20 00:00:00"),
            _row(3, "2026-05-01 00:00:00", "2026-07-01 00:00:00"),
            _row(4, "2026-07-05 00:00:00", False),
        ]
        result, _ = _run(rows)
        self.assertEqual([r["task_id"] for r in result], [2, 3, 4, 1])
        self.assertEqual([r["days_in_stage"] for r in result], [22, 11, 7, 1])

    def test_tie_broken_by_days_open_descending(self):
        # Equal days_in_stage (7); more-open task (id 11) sorts first.
        rows = [
            _row(10, "2026-07-01 00:00:00", "2026-07-05 00:00:00"),
            _row(11, "2026-06-01 00:00:00", "2026-07-05 00:00:00"),
        ]
        result, _ = _run(rows)
        self.assertEqual([r["task_id"] for r in result], [11, 10])

    def test_full_tie_broken_by_task_id_ascending(self):
        # Equal days_in_stage and days_open; lower task_id sorts first.
        rows = [
            _row(21, "2026-07-01 00:00:00", "2026-07-05 00:00:00"),
            _row(20, "2026-07-01 00:00:00", "2026-07-05 00:00:00"),
        ]
        result, _ = _run(rows)
        self.assertEqual([r["task_id"] for r in result], [20, 21])

    def test_false_last_stage_update_falls_back_to_create_date(self):
        rows = [_row(1, "2026-07-05 00:00:00", False)]
        result, _ = _run(rows)
        self.assertEqual(result[0]["days_open"], 7)
        self.assertEqual(result[0]["days_in_stage"], 7)

    def test_unparseable_date_degrades_to_none_without_crashing(self):
        # A malformed datetime string must not abort the whole report; the
        # offending day count degrades to None (and sorts last).
        rows = [_row(1, "not-a-date", "also-bad")]
        result, _ = _run(rows)
        self.assertEqual(result[0]["days_open"], None)
        self.assertEqual(result[0]["days_in_stage"], None)

    def test_default_now_uses_current_time(self):
        # With now=None the helper stamps the real UTC clock; a very old
        # create_date must yield a large positive day count.
        rows = [_row(1, "2000-01-01 00:00:00", "2000-01-01 00:00:00")]
        result, _ = _run(rows, now=None)
        self.assertGreater(result[0]["days_open"], 2000)
        self.assertGreater(result[0]["days_in_stage"], 2000)


class TestTaskAgingCommand(unittest.TestCase):
    """The built-in command delegates to the helper with keyword arguments."""

    def test_delegates_with_defaults(self):
        client = object()
        with patch(
            "odoo_sdk.commands.builtin.task_aging.get_task_aging",
            return_value=[],
        ) as mock_helper:
            result = TaskAgingCommand(client).execute()
        mock_helper.assert_called_once_with(
            client, project_id=None, stage=None, limit=20
        )
        self.assertEqual(result, [])

    def test_delegates_with_explicit_arguments(self):
        client = object()
        expected = [{"task_id": 1, "days_in_stage": 5}]
        with patch(
            "odoo_sdk.commands.builtin.task_aging.get_task_aging",
            return_value=expected,
        ) as mock_helper:
            result = TaskAgingCommand(client).execute(
                project_id=3, stage="review", limit=5
            )
        mock_helper.assert_called_once_with(
            client, project_id=3, stage="review", limit=5
        )
        self.assertEqual(result, expected)


if __name__ == "__main__":
    unittest.main()
