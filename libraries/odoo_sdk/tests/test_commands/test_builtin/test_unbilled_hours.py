"""Tests for the read-only ``unbilled_hours`` MCP tool (issue #244).

The billing-state fields on ``account.analytic.line`` are contributed by Odoo's
``sale_timesheet`` module, so their presence is edition-dependent. These tests
drive the command against a fake :class:`OdooExecutor` that answers ``fields_get``
from a configurable capability set, exercising every probe outcome (full,
fallback, and the neither-field error) at the wire level plus the date/project
filters, the empty-result shape, and TOON encoding.
"""

import os
import unittest
from typing import Any
from unittest.mock import patch

from odoo_sdk.client import OdooClient
from odoo_sdk.commands.builtin import UnbilledHoursCommand
from odoo_sdk.transport.executor import OdooExecutor
from odoo_sdk.utilities.odoo_helpers import (
    MISSING_UNBILLED_CAPABILITY_MESSAGE,
    get_unbilled_hours,
)

_MODEL = "account.analytic.line"
_INVOICE_ID = "timesheet_invoice_id"
_INVOICE_TYPE = "timesheet_invoice_type"


class _CapabilityExecutor(OdooExecutor):
    """Fake executor answering ``fields_get`` from a fixed capability set.

    ``fields_get`` echoes back metadata only for requested fields that are in
    ``present_fields`` (Odoo omits unknown fields), so the probe sees exactly the
    capabilities configured. ``search_read`` returns the canned ``rows`` and
    records the domain / kwargs so filter construction is asserted at the wire
    level. Any other call is a bug and fails loudly.
    """

    def __init__(self, present_fields: set[str], rows: list[dict]) -> None:
        self._present = set(present_fields)
        self._rows = rows
        self.calls: list[tuple[str, str, tuple, dict]] = []

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((model, method, args, kwargs))
        if method == "fields_get":
            requested = args[0]
            return {name: {"type": "x"} for name in requested if name in self._present}
        if method == "search_read":
            return list(self._rows)
        raise AssertionError(f"unexpected call: {model}.{method}")

    def search_read_call(self) -> tuple[str, str, tuple, dict]:
        matches = [call for call in self.calls if call[1] == "search_read"]
        assert len(matches) == 1, f"expected one search_read, got {matches}"
        return matches[0]


def _client(present_fields: set[str], rows: list[dict]) -> tuple:
    executor = _CapabilityExecutor(present_fields, rows)
    return OdooClient(executor=executor), executor


_FULL_ROWS = [
    {
        "id": 11,
        "date": "2026-07-01",
        "employee_id": [9, "Jane"],
        "project_id": [3, "Acme"],
        "task_id": [7, "Fix VAT"],
        "unit_amount": 2.5,
        "name": "Analysis",
        _INVOICE_TYPE: "billable_time",
    },
    {
        "id": 12,
        "date": "2026-07-02",
        "employee_id": [9, "Jane"],
        "project_id": [3, "Acme"],
        "task_id": False,
        "unit_amount": 3.2,
        "name": "Meeting",
        _INVOICE_TYPE: "non_billable",
    },
]


class TestFullSemantics(unittest.TestCase):
    """Both billing fields present -> full unbilled semantics."""

    def test_domain_fields_and_envelope(self):
        client, executor = _client({_INVOICE_ID, _INVOICE_TYPE}, _FULL_ROWS)

        result = get_unbilled_hours(client)

        model, method, args, kwargs = executor.search_read_call()
        self.assertEqual((model, method), (_MODEL, "search_read"))
        # Full mode filters on the invoice link and restricts to timesheet lines.
        self.assertEqual(
            args[0],
            [("project_id", "!=", False), (_INVOICE_ID, "=", False)],
        )
        # timesheet_invoice_type is projected so each row can report billability.
        self.assertIn(_INVOICE_TYPE, kwargs["fields"])
        self.assertEqual(kwargs["order"], "date asc")

        self.assertEqual(result["mode"], "full")
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["total_hours"], 5.7)
        self.assertEqual(
            result["lines"],
            [
                {
                    "id": 11,
                    "date": "2026-07-01",
                    "employee": "Jane",
                    "project": "Acme",
                    "task": "Fix VAT",
                    "hours": 2.5,
                    "name": "Analysis",
                    "invoice_type": "billable_time",
                },
                {
                    "id": 12,
                    "date": "2026-07-02",
                    "employee": "Jane",
                    "project": "Acme",
                    "task": False,
                    "hours": 3.2,
                    "name": "Meeting",
                    "invoice_type": "non_billable",
                },
            ],
        )

    def test_date_and_project_filters_appended(self):
        client, executor = _client({_INVOICE_ID, _INVOICE_TYPE}, [])

        get_unbilled_hours(
            client,
            start_date="2026-07-01",
            end_date="2026-07-31",
            project_id=3,
        )

        _, _, args, _ = executor.search_read_call()
        self.assertEqual(
            args[0],
            [
                ("project_id", "!=", False),
                (_INVOICE_ID, "=", False),
                ("date", ">=", "2026-07-01"),
                ("date", "<=", "2026-07-31"),
                ("project_id", "=", 3),
            ],
        )

    def test_total_hours_is_rounded(self):
        rows = [
            {"id": 1, "unit_amount": 0.1, _INVOICE_TYPE: "billable_time"},
            {"id": 2, "unit_amount": 0.2, _INVOICE_TYPE: "billable_time"},
        ]
        client, _ = _client({_INVOICE_ID, _INVOICE_TYPE}, rows)

        result = get_unbilled_hours(client)

        self.assertEqual(result["total_hours"], 0.3)

    def test_missing_unit_amount_counts_as_zero(self):
        rows = [{"id": 1, _INVOICE_TYPE: "billable_time"}]  # no unit_amount key
        client, _ = _client({_INVOICE_ID, _INVOICE_TYPE}, rows)

        result = get_unbilled_hours(client)

        self.assertEqual(result["total_hours"], 0.0)
        self.assertIsNone(result["lines"][0]["hours"])


class TestFallbackSemantics(unittest.TestCase):
    """Exactly one billing field present -> so_line fallback, no invoice_type."""

    _FALLBACK_ROWS = [
        {
            "id": 21,
            "date": "2026-07-03",
            "employee_id": [9, "Jane"],
            "project_id": [3, "Acme"],
            "task_id": [7, "Fix VAT"],
            "unit_amount": 4.0,
            "name": "Work",
        }
    ]

    def test_only_invoice_type_present_uses_so_line(self):
        client, executor = _client({_INVOICE_TYPE}, self._FALLBACK_ROWS)

        result = get_unbilled_hours(client)

        _, _, args, kwargs = executor.search_read_call()
        self.assertEqual(
            args[0],
            [("project_id", "!=", False), ("so_line", "=", False)],
        )
        # Fallback never projects the invoice-type field.
        self.assertNotIn(_INVOICE_TYPE, kwargs["fields"])
        self.assertEqual(result["mode"], "fallback")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["total_hours"], 4.0)
        self.assertNotIn("invoice_type", result["lines"][0])

    def test_only_invoice_id_present_still_falls_back(self):
        client, executor = _client({_INVOICE_ID}, [])

        result = get_unbilled_hours(client)

        _, _, args, _ = executor.search_read_call()
        self.assertIn(("so_line", "=", False), args[0])
        self.assertEqual(result["mode"], "fallback")


class TestNeitherFieldError(unittest.TestCase):
    """Neither billing field present -> a clear, boundary-formatted ValueError."""

    def test_raises_exact_message(self):
        client, executor = _client(set(), [])

        with self.assertRaises(ValueError) as ctx:
            get_unbilled_hours(client)

        self.assertEqual(str(ctx.exception), MISSING_UNBILLED_CAPABILITY_MESSAGE)
        # It fails on the probe alone: no search_read is ever issued.
        self.assertEqual([c[1] for c in executor.calls], ["fields_get"])


class TestDateValidation(unittest.TestCase):
    """Malformed dates raise a labelled ValueError before any query runs."""

    def test_bad_start_date(self):
        client, executor = _client({_INVOICE_ID, _INVOICE_TYPE}, [])
        with self.assertRaises(ValueError) as ctx:
            get_unbilled_hours(client, start_date="2026/07/01")
        self.assertIn("start_date", str(ctx.exception))
        self.assertEqual(executor.calls, [])  # rejected before the probe

    def test_bad_end_date(self):
        client, _ = _client({_INVOICE_ID, _INVOICE_TYPE}, [])
        with self.assertRaises(ValueError) as ctx:
            get_unbilled_hours(client, end_date="not-a-date")
        self.assertIn("end_date", str(ctx.exception))

    def test_rejects_basic_iso_without_dashes(self):
        # "20260701" parses via date.fromisoformat but is not canonical
        # YYYY-MM-DD, so it must be rejected to avoid silent mis-comparison.
        client, executor = _client({_INVOICE_ID, _INVOICE_TYPE}, [])
        with self.assertRaises(ValueError):
            get_unbilled_hours(client, start_date="20260701")
        self.assertEqual(executor.calls, [])


class TestEmptyResultShape(unittest.TestCase):
    """An empty match still returns the full, well-typed envelope."""

    def test_empty_full_mode(self):
        client, _ = _client({_INVOICE_ID, _INVOICE_TYPE}, [])
        result = get_unbilled_hours(client)
        self.assertEqual(
            result,
            {"mode": "full", "count": 0, "total_hours": 0.0, "lines": []},
        )


class TestCommandWrapper(unittest.TestCase):
    """The built-in command delegates to the helper and carries its metadata."""

    def test_execute_delegates_with_kwargs(self):
        client, executor = _client({_INVOICE_ID, _INVOICE_TYPE}, _FULL_ROWS)

        result = UnbilledHoursCommand(client).execute(
            start_date="2026-07-01", project_id=3
        )

        _, _, args, _ = executor.search_read_call()
        self.assertIn(("date", ">=", "2026-07-01"), args[0])
        self.assertIn(("project_id", "=", 3), args[0])
        self.assertEqual(result["mode"], "full")

    def test_metadata(self):
        command = UnbilledHoursCommand(object())
        self.assertEqual(command.name, "unbilled_hours")
        self.assertIn("unbilled", command.description)


class TestToonEncoding(unittest.TestCase):
    """The envelope survives TOON encoding when ODOO_TOON_OUTPUT is enabled."""

    def test_envelope_encodes_to_toon_string(self):
        from odoo_sdk.mcp.server import _to_toon

        client, _ = _client({_INVOICE_ID, _INVOICE_TYPE}, _FULL_ROWS)
        envelope = get_unbilled_hours(client)

        with patch.dict(os.environ, {"ODOO_TOON_OUTPUT": "1"}):
            encoded = _to_toon(envelope)

        self.assertIsInstance(encoded, str)
        self.assertIn("total_hours", encoded)


if __name__ == "__main__":
    unittest.main()
