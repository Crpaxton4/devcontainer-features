"""Sessionization reads the run record (#1065).

The test that matters is agreement: the same work, sessionized over the legacy
event store and over the run record, gives the same sessions, and with both
sources present it still gives ONE session per piece of work, never two.
Window boundaries are tested directly, including a zero-length run, because a
run counted twice or lost is a wrong invoice that nothing else reports.
"""

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
    runs.record_run(
        session_id=f"s{next(_session_counter)}",
        job_id="j",
        task_id=task,
        started_us=to_us(start),
        ended_us=to_us(end),
        outcome=outcome,
        tool_version="2.1.0",
        repo=repo,
    )


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

    def test_a_failed_run_is_still_measured_time(self, state, runs):
        add_run(runs, at(0), at(5), outcome=RunOutcome.FAILED)
        assert len(sessions(state)) == 1

    def test_task_filter_scopes_runs(self, state, runs):
        add_run(runs, at(0), at(5), task="1")
        add_run(runs, at(0), at(5), task="2")
        assert [w.task_id for w in sessions(state, task_id="2")] == ["2"]

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
        add_run(runs, at(0), at(30))
        (w,) = sessions(state)
        assert w.id == first
        assert session_key(w) == f"101|{first}"

    def test_a_run_only_window_has_a_stable_negative_id(self, state, runs):
        add_run(runs, at(0), at(5))
        (w,) = sessions(state)
        assert w.id == -to_us(at(0))
        assert sessions(state)[0].id == w.id

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
    def test_no_runs_db_means_events_only(self, tmp_path):
        state = LocalStateClient(
            make_state_db(tmp_path / "t.db")._db_path, runs_db=tmp_path / "none.db"
        )
        add_event(state, at(0))
        assert len(sessions(state)) == 1
        assert not (tmp_path / "none.db").exists()

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
        monkeypatch.setenv("ODOO_UNATTENDED", "0")
        assert not _is_dispatched_run()
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
