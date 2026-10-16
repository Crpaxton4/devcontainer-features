"""Sessionization reads the run record (#1065).

The test that matters is agreement: the same work, sessionized over the legacy
event store and over the run record, gives the same sessions, and with both
sources present it still gives ONE session per piece of work, never two.
Window boundaries are tested directly, including a zero-length run, because a
run counted twice or lost is a wrong invoice that nothing else reports.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest

from odoo_sdk.commands.dispatch_telemetry import _is_dispatched_run
from odoo_sdk.state import EventRecord, LocalStateClient, session_key
from odoo_sdk.state.run_sessions import from_us, to_us
from odoo_sdk.state.runs_store import RunOutcome, RunStore
from tests.support import make_state_db

UTC = timezone.utc
GAP = 3600
LO = datetime(2020, 1, 1, tzinfo=UTC)
HI = datetime(2030, 1, 1, tzinfo=UTC)
NINE = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)


def at(minutes: int) -> datetime:
    return NINE + timedelta(minutes=minutes)


@pytest.fixture
def runs(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    yield store
    store.close()


@pytest.fixture
def state(tmp_path, runs):
    make_state_db(tmp_path / "tracker.db")
    return LocalStateClient(tmp_path / "tracker.db", runs_db=tmp_path / "runs.db")


def add_event(state, ts, task="101", repo="", source="agent"):
    return state.add_event(
        EventRecord(
            id=None, source=source, timestamp=ts, task_ids=[task], repo=repo, pr_num=0
        )
    )


_session_counter = iter(range(10_000))


def add_run(runs, start, end, task="101", repo="", outcome=RunOutcome.COMPLETED):
    sid = f"s{next(_session_counter)}"
    runs.record_run(
        session_id=sid,
        job_id="j",
        task_id=task,
        started_us=to_us(start),
        ended_us=to_us(end),
        outcome=outcome,
        tool_version="2.1.0",
        repo=repo,
    )
    return sid


def sessions(state, **kw):
    return state.derive_sessions_overlapping(LO, HI, gap_secs=GAP, **kw)


def shape(windows):
    return [(w.task_id, w.started_at, w.ended_at) for w in windows]


def seed_work(state_or_runs, as_events: bool):
    """Two pieces of work on task 101 and one on 202, in either source."""
    pieces = [("101", 0, 40), ("101", 300, 330), ("202", 10, 20)]
    for task, a, b in pieces:
        if as_events:
            for minute in range(a, b + 1, 10):
                add_event(state_or_runs, at(minute), task=task)
        else:
            add_run(state_or_runs, at(a), at(b), task=task)


class TestAgreement:
    def test_events_and_run_record_give_the_same_sessions(self, tmp_path):
        legacy = make_state_db(tmp_path / "legacy.db")
        seed_work(legacy, as_events=True)
        measured_store = RunStore(tmp_path / "m_runs.db")
        measured = LocalStateClient(
            make_state_db(tmp_path / "m.db")._db_path, runs_db=tmp_path / "m_runs.db"
        )
        seed_work(measured_store, as_events=False)

        assert shape(sessions(measured)) == shape(sessions(legacy))
        assert len(sessions(measured)) == 3
        measured_store.close()

    def test_both_sources_for_the_same_work_collapse_not_double(self, state, runs):
        seed_work(state, as_events=True)
        seed_work(runs, as_events=False)
        windows = sessions(state)
        assert len(windows) == 3
        assert sorted(shape(windows)) == sorted(
            [
                ("101", at(0), at(40)),
                ("202", at(10), at(20)),
                ("101", at(300), at(330)),
            ]
        )

    def test_a_spread_task_is_two_sessions_with_two_contexts(self, state, runs):
        # Nine in the morning and again at four: two contexts, not one.
        add_run(runs, at(0), at(30))
        add_run(runs, at(420), at(450))
        assert len(sessions(state)) == 2


class TestMerge:
    def test_run_within_the_gap_of_events_joins_them(self, state, runs):
        add_event(state, at(0))
        add_run(runs, at(30), at(50))
        (w,) = sessions(state)
        assert (w.started_at, w.ended_at) == (at(0), at(50))

    def test_gap_is_the_existing_rule_exactly(self, state, runs):
        add_run(runs, at(0), at(10))
        add_run(runs, at(10) + timedelta(seconds=GAP), at(80))  # exactly the gap
        assert len(sessions(state)) == 1
        add_run(runs, at(300), at(310))
        assert len(sessions(state)) == 2

    def test_one_second_past_the_gap_splits(self, state, runs):
        add_run(runs, at(0), at(10))
        add_run(runs, at(10) + timedelta(seconds=GAP + 1), at(90))
        assert len(sessions(state)) == 2

    def test_a_run_bridging_two_event_sessions_makes_one(self, state, runs):
        add_event(state, at(0))
        add_event(state, at(200))
        assert len(sessions(state)) == 2
        add_run(runs, at(30), at(170))
        assert len(sessions(state)) == 1

    def test_tasks_stay_separate(self, state, runs):
        add_run(runs, at(0), at(30), task="1")
        add_run(runs, at(10), at(40), task="2")
        assert {w.task_id for w in sessions(state)} == {"1", "2"}

    def test_a_failed_run_is_kept_and_marked_not_dropped(self, state, runs):
        add_run(runs, at(0), at(50), outcome=RunOutcome.FAILED)
        (w,) = sessions(state)
        assert w.has_failed and w.duration_seconds == 3000

    def test_a_failed_run_beside_a_completed_one_marks_the_session(self, state, runs):
        add_run(runs, at(0), at(50), outcome=RunOutcome.FAILED)
        add_run(runs, at(60), at(70))
        (w,) = sessions(state)
        assert w.has_failed and w.duration_seconds == 4200

    def test_completed_runs_carry_no_mark(self, state, runs):
        add_run(runs, at(0), at(50))
        assert not sessions(state)[0].has_failed

    def test_task_filter_scopes_runs(self, state, runs):
        keep = add_run(runs, at(0), at(5), task="2")
        add_run(runs, at(0), at(5), task="1")
        add_run(runs, at(400), at(405), task="1")
        add_event(state, at(0), task="3")
        out = sessions(state, task_id="2")
        assert [(w.task_id, w.run_session_id) for w in out] == [("2", keep)]
        assert sessions(state, task_id="9") == []

    def test_repo_filter_applies_after_the_merge(self, state, runs):
        add_event(state, at(0), repo="")
        add_run(runs, at(10), at(20), repo="o/r")
        assert [w.repo for w in sessions(state, repo="o/r")] == ["o/r"]
        assert sessions(state, repo="x/y") == []

    def test_run_only_repo_less_session_uses_the_agentless_label(self, state, runs):
        add_run(runs, at(0), at(5))
        assert sessions(state, repo="")[0].repo == ""

    def test_window_overlap_is_inclusive_like_the_events_path(self, state, runs):
        add_run(runs, at(0), at(10))
        out = state.derive_sessions_overlapping(at(10), at(20), gap_secs=GAP)
        assert len(out) == 1
        assert state.derive_sessions_overlapping(at(11), at(20), gap_secs=GAP) == []


class TestIdentity:
    def test_a_window_with_events_keeps_the_smallest_event_id(self, state, runs):
        first = add_event(state, at(10)).id
        sid = add_run(runs, at(0), at(30))
        (w,) = sessions(state)
        assert w.id == first
        # Any window holding a run is keyed on its earliest run, not on events.
        assert session_key(w) == f"101|run:{sid}"

    def test_a_run_window_is_keyed_on_its_earliest_run(self, state, runs):
        sid = add_run(runs, at(0), at(5))
        (w,) = sessions(state)
        assert session_key(w) == f"101|run:{sid}"
        assert w.id == -to_us(at(0))

    def test_the_key_does_not_flip_when_an_event_joins_the_window(self, state, runs):
        add_run(runs, at(0), timedelta(seconds=100) + at(0))
        before = session_key(sessions(state)[0])
        add_event(state, at(0) + timedelta(seconds=3700))  # joins: gap 3600 exactly
        (w,) = sessions(state)
        assert w.ended_at == at(0) + timedelta(seconds=3700)
        assert session_key(w) == before

    def test_the_key_is_stable_across_reads(self, state, runs):
        add_run(runs, at(0), at(5))
        assert session_key(sessions(state)[0]) == session_key(sessions(state)[0])

    def test_repo_is_order_independent_and_matches_max_nullif(self, state, runs):
        add_run(runs, at(0), at(5), repo="")
        add_run(runs, at(6), at(9), repo="a/b")
        add_run(runs, at(10), at(12), repo="")
        assert sessions(state)[0].repo == "a/b"
        add_run(runs, at(13), at(14), repo="c/d")
        assert sessions(state)[0].repo == "c/d"

    def test_run_metadata_reaches_the_window(self, state, runs):
        runs.record_run(
            session_id="p",
            job_id="j",
            task_id="9",
            started_us=to_us(at(0)),
            ended_us=to_us(at(5)),
            outcome=RunOutcome.COMPLETED,
            tool_version="2",
            pr_num=77,
        )
        (w,) = sessions(state)
        assert (w.pr_num, w.category) == (77, "Development")


class TestGapPrecision:
    """Run-to-event gaps follow the events-only rule: ROUND(gap) > gap_secs splits."""

    def case(self, state, runs, extra_us):
        add_event(state, at(0))
        start = at(0) + timedelta(seconds=GAP, microseconds=extra_us)
        add_run(runs, start, start + timedelta(minutes=5))
        return len(sessions(state))

    def test_exactly_the_gap_joins(self, state, runs):
        assert self.case(state, runs, 0) == 1

    def test_just_under_half_a_second_over_rounds_down_and_joins(self, state, runs):
        assert self.case(state, runs, 490_000) == 1

    def test_half_a_second_over_rounds_up_and_splits(self, state, runs):
        assert self.case(state, runs, 500_000) == 2

    def test_a_second_over_splits(self, state, runs):
        assert self.case(state, runs, 1_000_000) == 2


class TestPrune:
    def plan(self, state):
        from odoo_sdk.tracking.prune import plan_prune

        return plan_prune(
            state,
            Mock(session_gap_secs=GAP),
            older_than_days=30,
            now=datetime(2026, 8, 1, tzinfo=UTC),
        )

    def upload(self, state, window):
        state.record_session_upload(
            session_key(window),
            7,
            1.0,
            task_id=window.task_id,
            started_at=window.started_at,
            ended_at=window.ended_at,
        )

    def test_a_pruned_run_session_keeps_its_ledger_row(self, state, runs):
        from odoo_sdk.tracking.prune import execute_prune

        add_run(runs, at(0), at(30))
        (w,) = sessions(state)
        self.upload(state, w)
        plan = self.plan(state)
        assert plan.retire_keys == []
        execute_prune(state, plan)
        # The row stays, so the session that still derives cannot bill twice.
        assert state.get_session_upload(session_key(w)) is not None
        assert session_key(sessions(state)[0]) == session_key(w)

    def test_a_mixed_session_keeps_its_ledger_row_when_events_go(self, state, runs):
        from odoo_sdk.tracking.prune import execute_prune

        add_event(state, at(10))
        add_run(runs, at(0), at(30))
        (w,) = sessions(state)
        self.upload(state, w)
        execute_prune(state, self.plan(state))
        assert state.get_session_upload(session_key(w)) is not None
        assert session_key(sessions(state)[0]) == session_key(w)

    def test_an_events_only_session_is_still_retired(self, state, runs):
        add_event(state, at(0))
        (w,) = sessions(state)
        self.upload(state, w)
        assert self.plan(state).retire_keys == [session_key(w)]


class TestWindowBoundaries:
    def ids(self, runs, lo, hi):
        return [r.session_id for r in runs.iter_runs(start_us=lo, end_us=hi)]

    def test_adjacent_windows_never_both_return_a_run(self, runs):
        add_run(runs, from_us(10), from_us(20))
        assert len(self.ids(runs, 10, 20)) == 1
        assert self.ids(runs, 20, 30) == []

    def test_a_zero_length_run_lands_in_exactly_one_window(self, runs):
        add_run(runs, from_us(20), from_us(20))
        windows = [(0, 10), (10, 20), (20, 30), (30, 40)]
        hits = [w for w in windows if self.ids(runs, *w)]
        assert hits == [(20, 30)]

    def test_a_zero_length_run_is_a_session_of_its_own(self, state, runs):
        add_run(runs, at(0), at(0))
        (w,) = sessions(state)
        assert w.started_at == w.ended_at == at(0)

    def test_microsecond_precision_survives_the_round_trip(self):
        us = to_us(at(0)) + 123_456
        assert to_us(from_us(us)) == us


class TestSourceAvailability:
    def test_a_named_runs_db_that_is_absent_is_said_plainly(self, tmp_path):
        state = LocalStateClient(
            make_state_db(tmp_path / "t.db")._db_path, runs_db=tmp_path / "none.db"
        )
        add_event(state, at(0))
        with pytest.raises(FileNotFoundError, match="none.db"):
            sessions(state)
        assert not (tmp_path / "none.db").exists()

    def test_the_default_location_absent_means_events_only(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ODOO_DISPATCH_STATE_DIR", str(tmp_path / "empty"))
        monkeypatch.setenv("ODOO_TASK_TRACKER_DIR", str(tmp_path))
        make_state_db(tmp_path / "tracker.db")
        add_event(LocalStateClient(), at(0))
        assert len(sessions(LocalStateClient())) == 1
        assert not (tmp_path / "empty").exists()

    def test_an_empty_runs_db_is_skipped_and_left_untouched(self, tmp_path):
        (tmp_path / "runs.db").write_bytes(b"")
        state = LocalStateClient(
            make_state_db(tmp_path / "t.db")._db_path, runs_db=tmp_path / "runs.db"
        )
        add_event(state, at(0))
        assert len(sessions(state)) == 1
        assert (tmp_path / "runs.db").stat().st_size == 0

    def test_reading_never_modifies_the_run_record(self, state, runs, tmp_path):
        add_run(runs, at(0), at(5))
        before = (tmp_path / "runs.db").read_bytes()
        sessions(state)
        assert (tmp_path / "runs.db").read_bytes() == before

    def test_a_corrupt_runs_db_raises_and_does_not_fall_back(self, tmp_path):
        (tmp_path / "runs.db").write_bytes(b"this is not a sqlite database" * 50)
        state = LocalStateClient(
            make_state_db(tmp_path / "t.db")._db_path, runs_db=tmp_path / "runs.db"
        )
        add_event(state, at(0))
        with pytest.raises(sqlite3.DatabaseError):
            sessions(state)

    def test_a_locked_runs_db_raises_and_does_not_fall_back(
        self, tmp_path, monkeypatch
    ):
        """A real lock: a journal-mode DELETE db held under BEGIN EXCLUSIVE.

        Proves the ``mode=ro`` read raises ``database is locked`` after the busy
        timeout (shortened here) instead of falling back to the event store. It
        does not prove behaviour under WAL, where readers are not blocked.
        """
        monkeypatch.setattr("odoo_sdk.state.run_sessions.BUSY_TIMEOUT_MS", 100)
        path = tmp_path / "locked.db"
        holder = sqlite3.connect(path, isolation_level=None)
        holder.execute("PRAGMA journal_mode=DELETE")
        holder.execute("CREATE TABLE runs (x)")
        holder.execute("BEGIN EXCLUSIVE")
        try:
            state = LocalStateClient(
                make_state_db(tmp_path / "t.db")._db_path, runs_db=path
            )
            add_event(state, at(0))
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                sessions(state)
        finally:
            holder.execute("ROLLBACK")
            holder.close()

    def test_read_runs_matches_iter_runs(self, state, runs, tmp_path):
        """The read-only path must agree with RunStore.iter_runs (the __new__ seam)."""
        from odoo_sdk.state.run_sessions import read_runs

        add_run(runs, at(0), at(5))
        add_run(runs, at(9), at(9))
        got = read_runs(tmp_path / "runs.db", 0, 2**62, expected=True)
        assert got == runs.iter_runs(start_us=0, end_us=2**62)
        assert len(got) == 2

    def test_a_naive_stored_timestamp_does_not_crash_the_merge(
        self, state, runs, tmp_path
    ):
        add_event(state, at(0))
        add_run(runs, at(10), at(20))
        raw = sqlite3.connect(tmp_path / "tracker.db")
        for stored in ("2026-06-01T09:00:00", "2026-06-01 09:00:00"):
            raw.execute("UPDATE events SET timestamp=?", (stored,))
            raw.commit()
            (w,) = sessions(state)
            assert w.started_at == at(0)
        raw.close()

    def test_an_explicit_db_path_ignores_a_stray_runs_db(self, tmp_path):
        store = RunStore(tmp_path / "runs.db")
        add_run(store, at(0), at(5))
        store.close()
        state = make_state_db(tmp_path / "t.db")
        assert sessions(state) == []

    def test_the_central_client_reads_the_state_root(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ODOO_DISPATCH_STATE_DIR", str(tmp_path))
        store = RunStore(tmp_path / "runs.db")
        add_run(store, at(0), at(5))
        store.close()
        make_state_db(tmp_path / "tracker.db")
        monkeypatch.setenv("ODOO_TASK_TRACKER_DIR", str(tmp_path))
        assert len(sessions(LocalStateClient())) == 1


class TestAgentEmission:
    def test_unattended_detection(self, monkeypatch):
        monkeypatch.delenv("ODOO_UNATTENDED", raising=False)
        assert not _is_dispatched_run()
        monkeypatch.setenv("ODOO_UNATTENDED", "")
        assert not _is_dispatched_run()
        monkeypatch.setenv("ODOO_UNATTENDED", "0")  # the guard hook: non-empty is set
        assert _is_dispatched_run()
        monkeypatch.setenv("ODOO_UNATTENDED", "1")
        assert _is_dispatched_run()

    def test_cli_cmd_emits_for_interactive_and_not_for_dispatched(self, monkeypatch):
        pytest.importorskip("fastmcp")
        pytest.importorskip("markdown_it")
        from odoo_sdk.cli import __main__ as cli

        registry = Mock()
        with patch.object(cli, "_emit_tool_event") as emit:
            monkeypatch.delenv("ODOO_UNATTENDED", raising=False)
            cli._emit_cmd_event(registry, "x", {}, {})
            assert emit.call_count == 1
            monkeypatch.setenv("ODOO_UNATTENDED", "1")
            cli._emit_cmd_event(registry, "x", {}, {})
            assert emit.call_count == 1

    def test_mcp_wrapper_emits_for_interactive_and_not_for_dispatched(
        self, monkeypatch, tmp_path
    ):
        pytest.importorskip("fastmcp")
        from odoo_sdk.commands import Registry
        from odoo_sdk.mcp.server import _event_emitting

        db = make_state_db(tmp_path / "t.db")
        tool = _event_emitting(
            lambda task_id: {"task_id": task_id}, "t", Registry(Mock(), state_client=db)
        )
        monkeypatch.delenv("ODOO_UNATTENDED", raising=False)
        tool(task_id=5)
        assert len(db.get_events()) == 1
        monkeypatch.setenv("ODOO_UNATTENDED", "1")
        tool(task_id=5)
        assert len(db.get_events()) == 1


class TestFailedRunVisibleAtUpload:
    def session(self, failed):
        return {
            "session_key": "1|run:s",
            "started_at": at(0).isoformat(),
            "ended_at": at(30).isoformat(),
            "duration_secs": 1800,
            **({"has_failed_run": True} if failed else {}),
        }

    def run_upload(self, failed, dry_run):
        pytest.importorskip("markdown_it")
        from odoo_sdk.billing import upload

        with (
            patch.object(upload, "reconcile_session", return_value=5) as rec,
            patch.object(upload, "_derived_description", return_value="did work"),
        ):
            row = upload._upload_one(
                Mock(), Mock(), 1, self.session(failed), dry_run, 0.0, 0.25
            )
        return row, rec

    def test_a_failed_run_is_flagged_in_the_row_and_the_line(self):
        row, rec = self.run_upload(True, dry_run=False)
        assert row["has_failed_run"] is True
        assert rec.call_args.args[4] == "[failed run] did work"

    def test_a_dry_run_shows_the_flag(self):
        row, _ = self.run_upload(True, dry_run=True)
        assert row["has_failed_run"] is True

    def test_a_completed_run_is_not_flagged(self):
        row, rec = self.run_upload(False, dry_run=False)
        assert "has_failed_run" not in row
        assert rec.call_args.args[4] == "did work"


class TestLegacyKeyFlip:
    """An event-keyed session that a run later joins still bills exactly once."""

    def upload(self, state, sessions_, lines):
        pytest.importorskip("markdown_it")
        from odoo_sdk.billing import timesheet, upload

        def create(client, st, task_id, description, hours, day):
            lines[len(lines) + 1] = {"unit_amount": hours, "name": description}
            return len(lines)

        def write(client, line_id, vals):
            lines[line_id].update(vals)

        rows = [
            {
                "task_id": w.task_id,
                "session_key": session_key(w),
                "started_at": w.started_at.isoformat(),
                "ended_at": w.ended_at.isoformat(),
                "duration_secs": w.duration_seconds,
            }
            for w in sessions_
        ]
        with (
            patch.object(timesheet, "_create_session_line", create),
            patch.object(timesheet, "_write_line", write),
            patch.object(timesheet, "_find_anchor", return_value=None),
            patch.object(upload, "_derived_description", return_value="did work"),
        ):
            return upload.upload_sessions(
                Mock(),
                state,
                rows,
                config=Mock(min_session_hours=0.5, round_session_hours=0.25),
            )

    def test_a_legacy_key_flip_bills_one_line_not_two(self, state, runs):
        pytest.importorskip("markdown_it")
        from odoo_sdk.billing.timesheet import ORPHANED_UPLOAD_NAME

        lines: dict[int, dict] = {}
        add_event(state, at(0))
        (before,) = sessions(state)
        assert session_key(before).startswith("101|") and "run:" not in session_key(
            before
        )
        self.upload(state, [before], lines)
        assert [v["unit_amount"] for v in lines.values()] == [0.5]

        sid = add_run(runs, at(30), at(50))  # lands inside the gap of the event
        (after,) = sessions(state)
        assert session_key(after) == f"101|run:{sid}"
        assert session_key(after) != session_key(before)
        self.upload(state, [after], lines)

        billed = [v for v in lines.values() if v["unit_amount"] > 0]
        assert len(billed) == 1 and billed[0]["unit_amount"] >= 0.5
        zeroed = [v for v in lines.values() if v["unit_amount"] == 0]
        assert [v["name"] for v in zeroed] == [ORPHANED_UPLOAD_NAME]
        assert [u["session_key"] for u in state.list_session_uploads()] == [
            session_key(after)
        ]
