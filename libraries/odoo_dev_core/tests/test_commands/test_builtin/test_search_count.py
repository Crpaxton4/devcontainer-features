"""Tests for the read-only ``search_count`` MCP command / tool (issue #445).

The command is driven through a real :class:`OdooClient` wrapping a recording
fake executor so the exact ``search_count`` model and serialized domain issued to
Odoo are asserted, and the returned integer is confirmed to flow back unchanged.
Counting never dumps records, so a large count is answered by one cheap call —
the whole point of the tool. No live Odoo is used.
"""

import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from odoo_client.client import OdooClient
from odoo_dev_core import default_registry
from odoo_client.transport.executor import OdooExecutor
from ...fakes import service


class _RecordingExecutor(OdooExecutor):
    """Fake executor recording every call and returning a canned count.

    Real ``OdooClient`` execution runs through this so the exact model, method,
    and serialized domain reaching Odoo can be asserted, while ``search_count``
    yields ``count`` without any record ever being read.
    """

    def __init__(self, count: int = 0) -> None:
        self._count = count
        self.calls: list[tuple[str, str, tuple[Any, ...], dict[str, Any]]] = []

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((model, method, args, kwargs))
        if method == "search_count":
            return self._count
        raise AssertionError(f"unexpected call: {model}.{method}")


def _client(count: int = 0) -> tuple[OdooClient, _RecordingExecutor]:
    executor = _RecordingExecutor(count)
    return OdooClient(executor=executor), executor


class TestSearchCountCommand(unittest.TestCase):
    """The command issues one ``search_count`` and returns its integer."""

    def test_registered_under_name(self):
        self.assertIn("search_count", default_registry().ids())

    def test_counts_with_domain(self):
        client, executor = _client(count=1234)
        result = service(client=client).execute(
            "search_count", model="project.task", domain=[("stage_id", "=", 3)]
        )
        self.assertEqual(result, 1234)
        self.assertEqual(len(executor.calls), 1)
        model, method, args, _ = executor.calls[0]
        self.assertEqual((model, method), ("project.task", "search_count"))
        self.assertEqual(args[0], [("stage_id", "=", 3)])

    def test_none_domain_counts_all_records(self):
        client, executor = _client(count=7)
        result = service(client=client).execute("search_count", model="res.partner")
        self.assertEqual(result, 7)
        model, method, args, _ = executor.calls[0]
        self.assertEqual((model, method), ("res.partner", "search_count"))
        # A ``None`` domain normalizes to the match-everything empty domain.
        self.assertEqual(args[0], [])

    def test_execute_is_read_only(self):
        client, executor = _client(count=0)
        service(client=client).execute("search_count", model="project.task")
        methods = {method for _, method, _, _ in executor.calls}
        self.assertEqual(methods, {"search_count"})


class TestSearchCountToolInvocation(unittest.TestCase):
    """The ``search_count`` command runs through the command service."""

    def test_command_end_to_end_through_client(self):
        client, executor = _client(count=9001)
        result = service(client=client).execute(
            "search_count", model="project.task", domain=[("stage_id", "!=", False)]
        )
        self.assertEqual(result, 9001)
        model, method, args, _ = executor.calls[0]
        self.assertEqual((model, method), ("project.task", "search_count"))
        self.assertEqual(args[0], [("stage_id", "!=", False)])


if __name__ == "__main__":
    unittest.main()
