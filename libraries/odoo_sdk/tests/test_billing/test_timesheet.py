"""Tests for the single-owner timesheet module (issue #181).

The unified module is the sole writer of ``account.analytic.line``. These tests
assert its idempotent operations behave — anchor adoption (kills #177) and the
idempotent per-session reconcile upsert (:func:`reconcile_session`, the sole
derived-upload hours-writer) — plus the orphan sweep (#353) and the AGENT event
producer (#180). A fake :class:`OdooExecutor` records calls so the scalar-id /
adopt-not-duplicate guarantees are checked at the wire level.
"""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from odoo_sdk.client import OdooClient
from odoo_sdk.state import LocalStateClient
from odoo_sdk.transport.errors import OdooServerError
from odoo_sdk.transport.executor import OdooExecutor
from odoo_sdk.billing.timesheet import (
    ORPHANED_UPLOAD_NAME,
    emit_agent_event,
    reconcile_session,
    resolve_employee_id,
    sweep_orphaned_uploads,
)
from tests.support import make_state_db

UTC = timezone.utc


def _tmp_db() -> LocalStateClient:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return make_state_db(Path(tmp.name))


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

    def __init__(self, anchors: list[dict] | None = None, new_id: Any = 500):
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
            client, db, task_id=10, session_key="10|1",
            description="[/] session 10|1", hours=1.5,
            started_at=datetime(2026, 7, 1, 9, 0, tzinfo=UTC),
            ended_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
        )
        self.assertEqual(tid, 500)
        creates = executor.by_method("create")
        self.assertEqual(len(creates), 1)
        vals = creates[0][2][0]
        self.assertEqual(vals["project_id"], 9)
        self.assertEqual(vals["task_id"], 10)
        self.assertEqual(vals["employee_id"], 7)
        self.assertEqual(vals["unit_amount"], 1.5)
        self.assertEqual(vals["date"], "2026-07-01")  # from started_at
        # Mapping recorded for idempotent re-runs, with task/window bounds.
        mapping = db.get_session_upload("10|1")
        self.assertEqual(mapping["timesheet_id"], 500)
        self.assertEqual(mapping["task_id"], "10")
        self.assertIsNotNone(mapping["started_at"])

    def test_create_result_list_is_unwrapped_to_scalar(self):
        # Regression for #170/#176: a batch-style ``[id]`` create result (a list)
        # breaks the SQLite bind and the later timesheet write. The fresh-line
        # create must unwrap it to a scalar int at the source.
        # list-wrapped id, as Odoo's batch create returns
        client, executor = self._client(anchors=[], new_id=[502])
        db = _tmp_db()
        tid = reconcile_session(
            client, db, task_id=10, session_key="10|9",
            description="[/] session 10|9", hours=1.0,
            started_at=datetime(2026, 7, 1, 9, 0, tzinfo=UTC),
            ended_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
        )
        self.assertIsInstance(tid, int)
        self.assertEqual(tid, 502)
        self.assertEqual(db.get_session_upload("10|9")["timesheet_id"], 502)

    def test_adopts_existing_anchor(self):
        client, executor = self._client(anchors=[{"id": 88}])
        db = _tmp_db()
        tid = reconcile_session(
            client, db, task_id=10, session_key="10|1",
            description="[/] done", hours=2.0,
            started_at=datetime(2026, 7, 2, 9, 0, tzinfo=UTC),
            ended_at=datetime(2026, 7, 2, 10, 0, tzinfo=UTC),
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
        self.assertEqual(db.get_session_upload("10|1")["timesheet_id"], 88)

    def test_rewrites_mapped_row_ignoring_anchor(self):
        # A recorded mapping wins over any anchor: the mapped row is rewritten.
        client, executor = self._client(anchors=[{"id": 88}])
        db = _tmp_db()
        db.record_session_upload("10|1", 200, 1.0)
        tid = reconcile_session(
            client, db, task_id=10, session_key="10|1",
            description="[/] x", hours=3.0,
            started_at=datetime(2026, 7, 3, 9, 0, tzinfo=UTC),
            ended_at=datetime(2026, 7, 3, 10, 0, tzinfo=UTC),
        )
        self.assertEqual(tid, 200)  # mapped id, not the anchor's 88
        writes = executor.by_method("write")
        self.assertEqual(writes[0][2][0], [200])
        self.assertEqual(executor.by_method("create"), [])

    def test_idempotent_rerun_rewrites_same_row(self):
        client, executor = self._client(anchors=[], new_id=500)
        db = _tmp_db()
        first = reconcile_session(
            client, db, task_id=10, session_key="10|1",
            description="[/] a", hours=1.0,
            started_at=datetime(2026, 7, 1, 9, 0, tzinfo=UTC),
            ended_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
        )
        second = reconcile_session(
            client, db, task_id=10, session_key="10|1",
            description="[/] b", hours=2.5,
            started_at=datetime(2026, 7, 1, 9, 0, tzinfo=UTC),
            ended_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
        )
        self.assertEqual(first, second)  # same timesheet id
        self.assertEqual(len(executor.by_method("create")), 1)  # created only once
        # Second run rewrote the same row with updated hours.
        self.assertEqual(db.get_session_upload("10|1")["hours"], 2.5)
        last_write = executor.by_method("write")[-1]
        self.assertEqual(last_write[2][0], [500])
        self.assertEqual(last_write[2][1]["unit_amount"], 2.5)

    def test_adopt_records_mapping_before_renaming_anchor(self):
        # #582 idempotency: the ledger mapping must be recorded BEFORE the adopted
        # anchor is renamed. Otherwise a crash between the write (which renames the
        # anchor out of _find_anchor's reach) and the record leaves a retry unable
        # to re-find the row, and the create branch double-bills. We assert the
        # ordering directly: at the instant the anchor rewrite hits the wire, the
        # mapping already exists.
        db = _tmp_db()
        observed = {}

        class _OrderingExecutor(_SessionExecutor):
            def execute(self, model, method, *args, **kwargs):
                if method == "write":
                    observed["mapping_at_write"] = db.get_session_upload("10|1")
                return super().execute(model, method, *args, **kwargs)

        executor = _OrderingExecutor(anchors=[{"id": 88}])
        reconcile_session(
            OdooClient(executor=executor), db, task_id=10, session_key="10|1",
            description="[/] done", hours=2.0,
            started_at=datetime(2026, 7, 2, 9, 0, tzinfo=UTC),
            ended_at=datetime(2026, 7, 2, 10, 0, tzinfo=UTC),
        )
        self.assertIsNotNone(observed["mapping_at_write"])  # recorded first
        self.assertEqual(observed["mapping_at_write"]["timesheet_id"], 88)

    def test_adopt_crash_before_ledger_does_not_double_bill_on_retry(self):
        # #582: the legacy-adopt path records the mapping BEFORE renaming the
        # anchor, so a fault mid-step (here the anchor rewrite crashes) still
        # leaves a persisted mapping. A retry re-finds the row via the mapped
        # branch and rewrites it in place — it never bills a SECOND line.
        db = _tmp_db()
        start = datetime(2026, 7, 2, 9, 0, tzinfo=UTC)
        end = datetime(2026, 7, 2, 10, 0, tzinfo=UTC)

        class _CrashOnWrite(_SessionExecutor):
            def execute(self, model, method, *args, **kwargs):
                if method == "write":
                    raise OdooServerError("crash mid anchor rename")
                return super().execute(model, method, *args, **kwargs)

        crashing = _CrashOnWrite(anchors=[{"id": 88}])
        with self.assertRaises(OdooServerError):
            reconcile_session(
                OdooClient(executor=crashing), db, task_id=10, session_key="10|1",
                description="[/] done", hours=2.0, started_at=start, ended_at=end,
            )
        # Mapping persisted before the failing rename: the retry is recoverable.
        self.assertEqual(db.get_session_upload("10|1")["timesheet_id"], 88)

        # Retry: the anchor can no longer be adopted (renamed / absent), but the
        # mapping routes to the mapped branch — a rewrite of row 88, not a create.
        retry = _SessionExecutor(anchors=[], new_id=999)
        tid = reconcile_session(
            OdooClient(executor=retry), db, task_id=10, session_key="10|1",
            description="[/] done", hours=2.0, started_at=start, ended_at=end,
        )
        self.assertEqual(tid, 88)  # same row, never the would-be new 999
        self.assertEqual(retry.by_method("create"), [])  # no second line billed
        self.assertEqual(retry.by_method("search_read"), [])  # mapped: no anchor lookup
        self.assertEqual(retry.by_method("write")[-1][2][0], [88])

    def test_write_ids_are_flat_scalars_not_nested_lists(self):
        # Regression for #193 (resurfaced #176/#167): the upload path's
        # ``reconcile_session`` -> ``account.analytic.line.write`` must pass the
        # ids as a FLAT list of scalar ints. A double-wrapped ``[[id]]`` makes
        # Odoo ``browse([[id]])`` so ``record._ids[0]`` is itself a list; the
        # stock timesheet write hashes it as a field-cache key and dies with
        # ``TypeError: unhashable type: 'list'``. The strict fake reproduces
        # that browse-and-hash exactly; the mapped-row branch drives the write.
        executor = _StrictBrowseHashExecutor()
        client = OdooClient(executor=executor)
        db = _tmp_db()
        db.record_session_upload("10|1", 50, 1.0)

        # The old ``[[50]]`` shape would raise here; the fix keeps it flat.
        result = reconcile_session(
            client, db, task_id=10, session_key="10|1",
            description="[/] Done", hours=1.5,
            started_at=datetime(2026, 7, 1, 9, 0, tzinfo=UTC),
            ended_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
        )

        self.assertEqual(result, 50)
        ids_arg = executor.write_ids
        self.assertEqual(ids_arg, [50])
        # Every id handed to ``write`` must be a hashable scalar, never a list.
        for rec_id in ids_arg:
            self.assertNotIsInstance(rec_id, list)
            self.assertIsInstance(rec_id, int)


class _SweepExecutor(OdooExecutor):
    """Fake executor recording ``account.analytic.line`` writes for the sweep."""

    def __init__(self):
        self.writes: list[tuple] = []

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        if method == "write":
            self.writes.append((args[0], args[1]))
            return True
        raise AssertionError(f"unexpected call: {model}.{method}")


class TestSweepOrphanedUploads(unittest.TestCase):
    """#353: the sweep zeroes and retires upload mappings that no longer derive."""

    def _client(self):
        executor = _SweepExecutor()
        return OdooClient(executor=executor), executor

    def _window(self):
        return (
            datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
            datetime(2026, 6, 2, 0, 0, tzinfo=UTC),
        )

    def _record(self, db, key, tid, task_id, day):
        db.record_session_upload(
            key, tid, 1.0, task_id=task_id,
            started_at=datetime(2026, 6, day, 9, 0, tzinfo=UTC),
            ended_at=datetime(2026, 6, day, 10, 0, tzinfo=UTC),
        )

    def test_still_deriving_mapping_untouched(self):
        client, executor = self._client()
        db = _tmp_db()
        self._record(db, "10|1", 500, "10", day=1)
        lo, hi = self._window()
        retired = sweep_orphaned_uploads(
            client, db, derived_keys={"10|1"}, derived_task_ids={"10"},
            window_lo=lo, window_hi=hi,
        )
        self.assertEqual(retired, 0)
        self.assertEqual(executor.writes, [])
        self.assertIsNotNone(db.get_session_upload("10|1"))

    def test_orphan_in_window_is_zeroed_and_retired(self):
        client, executor = self._client()
        db = _tmp_db()
        self._record(db, "10|1", 500, "10", day=1)  # merged-away session
        lo, hi = self._window()
        retired = sweep_orphaned_uploads(
            client, db, derived_keys=set(), derived_task_ids={"10"},
            window_lo=lo, window_hi=hi,
        )
        self.assertEqual(retired, 1)
        ids, vals = executor.writes[0]
        self.assertEqual(ids, [500])
        self.assertEqual(vals["unit_amount"], 0.0)
        self.assertEqual(vals["name"], ORPHANED_UPLOAD_NAME)
        self.assertIsNone(db.get_session_upload("10|1"))  # mapping retired

    def test_orphan_outside_window_preserved(self):
        # A mapping whose window does not overlap the queried window is left alone
        # even when it is not in the derived set (it was simply out of range).
        client, executor = self._client()
        db = _tmp_db()
        db.record_session_upload(
            "10|1", 500, 1.0, task_id="10",
            started_at=datetime(2026, 7, 1, 9, 0, tzinfo=UTC),
            ended_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
        )
        lo, hi = self._window()  # June window; mapping is in July
        retired = sweep_orphaned_uploads(
            client, db, derived_keys=set(), derived_task_ids={"10"},
            window_lo=lo, window_hi=hi,
        )
        self.assertEqual(retired, 0)
        self.assertIsNotNone(db.get_session_upload("10|1"))

    def test_legacy_key_retired_when_task_in_window(self):
        # A pre-#352 3-part key (NULL bounds) can never re-derive; it is retired
        # deliberately when its task prefix is in the current window's derived set.
        client, executor = self._client()
        db = _tmp_db()
        db.record_session_upload("10|owner/repo|1", 500, 1.0)  # legacy, no bounds
        lo, hi = self._window()
        retired = sweep_orphaned_uploads(
            client, db, derived_keys={"10|3"}, derived_task_ids={"10"},
            window_lo=lo, window_hi=hi,
        )
        self.assertEqual(retired, 1)
        self.assertEqual(executor.writes[0][1]["unit_amount"], 0.0)
        self.assertIsNone(db.get_session_upload("10|owner/repo|1"))

    def test_legacy_key_preserved_when_task_absent(self):
        client, executor = self._client()
        db = _tmp_db()
        db.record_session_upload("99|owner/repo|1", 500, 1.0)  # legacy, other task
        lo, hi = self._window()
        retired = sweep_orphaned_uploads(
            client, db, derived_keys={"10|3"}, derived_task_ids={"10"},
            window_lo=lo, window_hi=hi,
        )
        self.assertEqual(retired, 0)
        self.assertIsNotNone(db.get_session_upload("99|owner/repo|1"))


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
