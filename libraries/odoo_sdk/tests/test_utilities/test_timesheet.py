"""Tests for the single-owner timesheet module (issue #181).

The unified module is the sole writer of ``account.analytic.line``. These tests
assert its two idempotent operations behave — anchor adoption (kills #177) and
the idempotent reconcile upsert — plus the AGENT event producer (#180). A fake
:class:`OdooExecutor` records calls so the scalar-id / adopt-not-duplicate
guarantees are checked at the wire level.
"""

import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import patch

from odoo_sdk.client import OdooClient
from odoo_sdk.state import LocalStateClient
from odoo_sdk.transport.executor import OdooExecutor
from odoo_sdk.utilities.timesheet import (
    ANCHOR_NAME,
    emit_agent_event,
    ensure_anchor,
    reconcile,
)


def _tmp_db() -> LocalStateClient:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return LocalStateClient(db_path=Path(tmp.name))


class _AnchorExecutor(OdooExecutor):
    """Fake executor for the anchor search/create/write flow.

    ``search_read`` returns the seeded existing rows so adoption can be
    exercised; ``create`` mimics Odoo's single-dict → scalar id semantics; every
    call is recorded so the test can assert exactly one create ever fires.
    """

    def __init__(self, existing: list[dict] | None = None, new_id: Any = 123):
        self._existing = existing or []
        self._new_id = new_id
        self.calls: list[tuple] = []

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((model, method, args, kwargs))
        if method == "search_read":
            return list(self._existing)
        if method == "create":
            (vals,) = args
            # Single-dict create → scalar id (batch would be a list).
            return [self._new_id] if isinstance(vals, list) else self._new_id
        if method == "write":
            return True
        raise AssertionError(f"unexpected call: {model}.{method}")

    def creates(self) -> list[tuple]:
        return [c for c in self.calls if c[1] == "create"]

    def writes(self) -> list[tuple]:
        return [c for c in self.calls if c[1] == "write"]


class TestEnsureAnchor(unittest.TestCase):
    def test_creates_anchor_when_none_exists(self):
        executor = _AnchorExecutor(existing=[], new_id=99)
        client = OdooClient(executor=executor)
        anchor_id = ensure_anchor(
            client, task_id=10, project_id=5, employee_id=3, today=date(2026, 7, 1)
        )
        self.assertEqual(anchor_id, 99)
        creates = executor.creates()
        self.assertEqual(len(creates), 1)
        vals = creates[0][2][0]
        self.assertEqual(vals["name"], ANCHOR_NAME)
        self.assertEqual(vals["task_id"], 10)
        self.assertEqual(vals["project_id"], 5)
        self.assertEqual(vals["employee_id"], 3)
        self.assertEqual(vals["date"], "2026-07-01")
        self.assertEqual(vals["unit_amount"], 0.0)

    def test_returns_scalar_id_not_list(self):
        # Regression for #170/#176: a batch create yields ``[id]`` (a list) that
        # breaks the SQLite bind and the later timesheet write. The single-dict
        # create must be issued and the result unwrapped to a scalar int.
        executor = _AnchorExecutor(existing=[], new_id=77)
        client = OdooClient(executor=executor)
        anchor_id = ensure_anchor(
            client, task_id=1, project_id=2, employee_id=3, today=date(2026, 7, 1)
        )
        self.assertIsInstance(anchor_id, int)
        self.assertEqual(anchor_id, 77)

    def test_unwraps_list_create_result_defensively(self):
        # A batch-style ``[id]`` result (should never happen for a single-dict
        # create, but Odoo variants differ) is unwrapped to a scalar so the
        # #170/#176 crash cannot resurface downstream.
        executor = _AnchorExecutor(existing=[], new_id=42)

        def _list_execute(model, method, *args, **kwargs):
            executor.calls.append((model, method, args, kwargs))
            if method == "search_read":
                return []
            if method == "create":
                return [42]  # list-wrapped result
            raise AssertionError(method)

        client = OdooClient(executor=executor)
        with patch.object(client, "execute", _list_execute):
            anchor_id = ensure_anchor(
                client, task_id=1, project_id=2, employee_id=3, today=date(2026, 7, 1)
            )
        self.assertIsInstance(anchor_id, int)
        self.assertEqual(anchor_id, 42)

    def test_adopts_existing_anchor_instead_of_duplicating(self):
        # #177: a second start must reuse the existing "[/] Work in progress"
        # row rather than create a duplicate placeholder.
        executor = _AnchorExecutor(existing=[{"id": 55}])
        client = OdooClient(executor=executor)
        anchor_id = ensure_anchor(
            client, task_id=10, project_id=5, employee_id=3, today=date(2026, 7, 1)
        )
        self.assertEqual(anchor_id, 55)
        self.assertEqual(executor.creates(), [])  # never creates a second row

    def test_search_keys_on_task_and_marker(self):
        executor = _AnchorExecutor(existing=[])
        client = OdooClient(executor=executor)
        ensure_anchor(
            client, task_id=42, project_id=1, employee_id=1, today=date(2026, 7, 1)
        )
        search = next(c for c in executor.calls if c[1] == "search_read")
        domain = search[2][0]
        self.assertIn(("task_id", "=", 42), domain)
        self.assertIn(("name", "=", ANCHOR_NAME), domain)


class TestReconcile(unittest.TestCase):
    def test_writes_hours_and_description_onto_active_anchor(self):
        executor = _AnchorExecutor()
        client = OdooClient(executor=executor)
        db = _tmp_db()
        db.create_session(10, "Bug", 5, "Proj", timesheet_id=50)
        result = reconcile(client, db, task_id=10, description="[/] Done", elapsed_hours=1.5)
        self.assertEqual(result, 50)
        writes = executor.writes()
        self.assertEqual(len(writes), 1)
        ids_arg, vals_arg = writes[0][2]
        self.assertEqual(ids_arg, [[50]])
        self.assertEqual(vals_arg, {"unit_amount": 1.5, "name": "[/] Done"})

    def test_falls_back_to_odoo_lookup_when_no_active_session(self):
        # Reconcile from a TUI upload has no active FSM session; it must resolve
        # the anchor from Odoo and still write the single row.
        executor = _AnchorExecutor(existing=[{"id": 88}])
        client = OdooClient(executor=executor)
        db = _tmp_db()
        result = reconcile(client, db, task_id=10, description="[/] X", elapsed_hours=2.0)
        self.assertEqual(result, 88)
        self.assertEqual(len(executor.writes()), 1)

    def test_no_op_when_no_anchor_found(self):
        executor = _AnchorExecutor(existing=[])
        client = OdooClient(executor=executor)
        db = _tmp_db()
        result = reconcile(client, db, task_id=10, description="[/] X", elapsed_hours=2.0)
        self.assertIsNone(result)
        self.assertEqual(executor.writes(), [])

    def test_reconcile_is_idempotent_single_row(self):
        # Re-running reconcile writes the same one anchor row, never a new one.
        executor = _AnchorExecutor()
        client = OdooClient(executor=executor)
        db = _tmp_db()
        db.create_session(10, "Bug", 5, "Proj", timesheet_id=50)
        reconcile(client, db, task_id=10, description="[/] A", elapsed_hours=1.0)
        reconcile(client, db, task_id=10, description="[/] B", elapsed_hours=2.0)
        writes = executor.writes()
        self.assertEqual(len(writes), 2)
        for write in writes:
            self.assertEqual(write[2][0], [[50]])  # same id both times
        self.assertEqual(executor.creates(), [])  # never creates


class TestEmitAgentEvent(unittest.TestCase):
    def test_persists_agent_event(self):
        db = _tmp_db()
        record = emit_agent_event(db, task_id=10, subject="start_task: Bug")
        self.assertEqual(record.source, "agent")
        self.assertEqual(record.task_ids, ["10"])
        self.assertEqual(record.subject, "start_task: Bug")
        events = db.get_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].source, "agent")

    def test_carries_payload(self):
        db = _tmp_db()
        record = emit_agent_event(
            db, task_id=7, subject="note", payload={"detail": "x"}
        )
        self.assertEqual(record.payload, {"detail": "x"})

    def test_repo_less_so_it_groups_under_sentinel(self):
        db = _tmp_db()
        record = emit_agent_event(db, task_id=1, subject="s")
        self.assertEqual(record.repo, "")


if __name__ == "__main__":
    unittest.main()
