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
    reconcile_session,
    resolve_employee_id,
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
        db.create_run(10, "Bug", 5, "Proj", timesheet_id=50)
        result = reconcile(client, db, task_id=10, description="[/] Done", elapsed_hours=1.5)
        self.assertEqual(result, 50)
        writes = executor.writes()
        self.assertEqual(len(writes), 1)
        ids_arg, vals_arg = writes[0][2]
        self.assertEqual(ids_arg, [50])
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
        db.create_run(10, "Bug", 5, "Proj", timesheet_id=50)
        reconcile(client, db, task_id=10, description="[/] A", elapsed_hours=1.0)
        reconcile(client, db, task_id=10, description="[/] B", elapsed_hours=2.0)
        writes = executor.writes()
        self.assertEqual(len(writes), 2)
        for write in writes:
            self.assertEqual(write[2][0], [50])  # same id both times
        self.assertEqual(executor.creates(), [])  # never creates

    def test_write_ids_are_flat_scalars_not_nested_lists(self):
        # Regression for #193 (resurfaced #176/#167): the upload path's reconcile
        # -> ``account.analytic.line.write`` must pass the ids as a FLAT list of
        # scalar ints. A double-wrapped ``[[id]]`` makes Odoo ``browse([[id]])``,
        # so ``record._ids[0]`` is itself a list; the stock timesheet write hashes
        # it as a field-cache key and dies with ``TypeError: unhashable type:
        # 'list'``. The strict fake below mimics that browse-and-hash exactly.
        executor = _StrictBrowseHashExecutor()
        client = OdooClient(executor=executor)
        db = _tmp_db()
        db.create_run(10, "Bug", 5, "Proj", timesheet_id=50)

        # The old ``[[50]]`` shape would raise here; the fix keeps it flat.
        result = reconcile(
            client, db, task_id=10, description="[/] Done", elapsed_hours=1.5
        )

        self.assertEqual(result, 50)
        ids_arg = executor.write_ids
        self.assertEqual(ids_arg, [50])
        # Every id handed to ``write`` must be a hashable scalar, never a list.
        for rec_id in ids_arg:
            self.assertNotIsInstance(rec_id, list)
            self.assertIsInstance(rec_id, int)


class _StrictBrowseHashExecutor(OdooExecutor):
    """Fake executor that reproduces Odoo's browse-and-hash on ``write`` ids.

    Odoo turns the write's positional ids list into a recordset and later uses
    each id as a *dict key* when reading a related field's cache
    (``field_cache[record._ids[0]]``). A nested ``[id]`` element is therefore an
    unhashable dict key. This fake hashes each id exactly the way the ORM would,
    so a double-wrapped ``[[id]]`` raises ``TypeError: unhashable type: 'list'``
    (the #193 crash) while a flat ``[id]`` passes.
    """

    def __init__(self, existing: list[dict] | None = None):
        self._existing = existing or []
        self.write_ids: Any = None

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        if method == "search_read":
            return list(self._existing)
        if method == "write":
            ids = args[0]
            self.write_ids = ids
            # Mimic ``field_cache[record._ids[0]]``: each id becomes a dict key.
            # A list id is unhashable and raises exactly the #193 TypeError.
            {rec_id: None for rec_id in ids}
            return True
        raise AssertionError(f"unexpected call: {model}.{method}")


class _SessionExecutor(OdooExecutor):
    """Fake executor for the ``reconcile_session`` create/adopt/write flow.

    ``search_read`` on ``account.analytic.line`` returns the seeded anchor rows;
    ``search_read`` on ``hr.employee`` resolves an employee id; ``read`` on
    ``project.task`` resolves the owning project; ``create`` mimics the scalar-id
    semantics. Every call is recorded so branch selection can be asserted.
    """

    def __init__(self, anchors: list[dict] | None = None, new_id: int = 500):
        self._anchors = anchors or []
        self._new_id = new_id
        self.calls: list[tuple] = []
        self.uid = 1

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((model, method, args, kwargs))
        if model == "hr.employee" and method == "search_read":
            return [{"id": 7}]
        if model == "project.task" and method == "read":
            return [{"id": args[0][0], "project_id": [9, "Proj"]}]
        if method == "search_read":
            return list(self._anchors)
        if method == "create":
            return self._new_id
        if method == "write":
            return True
        raise AssertionError(f"unexpected call: {model}.{method}")

    def by_method(self, method: str) -> list[tuple]:
        return [c for c in self.calls if c[1] == method]


class TestResolveEmployeeId(unittest.TestCase):
    def test_fetches_and_caches(self):
        executor = _SessionExecutor()
        client = OdooClient(executor=executor)
        db = _tmp_db()
        first = resolve_employee_id(client, db)
        self.assertEqual(first, 7)
        self.assertEqual(db.get_setting("employee_id"), "7")
        # Second call is served from cache: no further hr.employee lookup.
        second = resolve_employee_id(client, db)
        self.assertEqual(second, 7)
        lookups = [c for c in executor.calls if c[0] == "hr.employee"]
        self.assertEqual(len(lookups), 1)

    def test_uses_cached_value(self):
        executor = _SessionExecutor()
        client = OdooClient(executor=executor)
        db = _tmp_db()
        db.set_setting("employee_id", "42")
        self.assertEqual(resolve_employee_id(client, db), 42)
        self.assertEqual([c for c in executor.calls if c[0] == "hr.employee"], [])


class TestReconcileSession(unittest.TestCase):
    def _client(self, **kw):
        executor = _SessionExecutor(**kw)
        return OdooClient(executor=executor), executor

    def test_creates_fresh_line_when_no_mapping_or_anchor(self):
        client, executor = self._client(anchors=[], new_id=500)
        db = _tmp_db()
        tid = reconcile_session(
            client, db, task_id=10, session_key="10|r|1",
            description="[/] session 10|r|1", hours=1.5, day=date(2026, 7, 1),
        )
        self.assertEqual(tid, 500)
        creates = executor.by_method("create")
        self.assertEqual(len(creates), 1)
        vals = creates[0][2][0]
        self.assertEqual(vals["project_id"], 9)
        self.assertEqual(vals["task_id"], 10)
        self.assertEqual(vals["employee_id"], 7)
        self.assertEqual(vals["unit_amount"], 1.5)
        self.assertEqual(vals["date"], "2026-07-01")
        # Mapping recorded for idempotent re-runs.
        self.assertEqual(db.get_session_upload("10|r|1")["timesheet_id"], 500)

    def test_adopts_existing_anchor(self):
        client, executor = self._client(anchors=[{"id": 88}])
        db = _tmp_db()
        tid = reconcile_session(
            client, db, task_id=10, session_key="10|r|1",
            description="[/] done", hours=2.0, day=date(2026, 7, 2),
        )
        self.assertEqual(tid, 88)
        self.assertEqual(executor.by_method("create"), [])  # adopts, never creates
        writes = executor.by_method("write")
        self.assertEqual(len(writes), 1)
        ids_arg, vals_arg = writes[0][2]
        self.assertEqual(ids_arg, [88])
        self.assertEqual(vals_arg["unit_amount"], 2.0)
        self.assertEqual(vals_arg["name"], "[/] done")
        self.assertEqual(vals_arg["date"], "2026-07-02")
        self.assertEqual(db.get_session_upload("10|r|1")["timesheet_id"], 88)

    def test_rewrites_mapped_row_ignoring_anchor(self):
        # A recorded mapping wins over any anchor: the mapped row is rewritten.
        client, executor = self._client(anchors=[{"id": 88}])
        db = _tmp_db()
        db.record_session_upload("10|r|1", 200, 1.0)
        tid = reconcile_session(
            client, db, task_id=10, session_key="10|r|1",
            description="[/] x", hours=3.0, day=date(2026, 7, 3),
        )
        self.assertEqual(tid, 200)  # mapped id, not the anchor's 88
        writes = executor.by_method("write")
        self.assertEqual(writes[0][2][0], [200])
        self.assertEqual(executor.by_method("create"), [])

    def test_idempotent_rerun_rewrites_same_row(self):
        client, executor = self._client(anchors=[], new_id=500)
        db = _tmp_db()
        first = reconcile_session(
            client, db, task_id=10, session_key="10|r|1",
            description="[/] a", hours=1.0, day=date(2026, 7, 1),
        )
        second = reconcile_session(
            client, db, task_id=10, session_key="10|r|1",
            description="[/] b", hours=2.5, day=date(2026, 7, 1),
        )
        self.assertEqual(first, second)  # same timesheet id
        self.assertEqual(len(executor.by_method("create")), 1)  # created only once
        # Second run rewrote the same row with updated hours.
        self.assertEqual(db.get_session_upload("10|r|1")["hours"], 2.5)
        last_write = executor.by_method("write")[-1]
        self.assertEqual(last_write[2][0], [500])
        self.assertEqual(last_write[2][1]["unit_amount"], 2.5)


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
