"""Tests for the dispatcher persistence layer (#1065): state root, migrations,
job store, run record and artifact bundle. Every schema is built by the
production migration runner, never by a test-local copy of the DDL."""

import json
import os
import sqlite3
import threading
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from odoo_sdk.state import dispatch_state
from odoo_sdk.state.artifacts import (
    BUNDLE_FILES,
    collect_artifacts,
    ensure_bundle_dir,
)
from odoo_sdk.state.jobs import (
    DeadReason,
    DuplicateTaskInFlightError,
    InvalidJobTransitionError,
    JobState,
    JobStore,
)
from odoo_sdk.state.migrations import (
    Migration,
    MigrationError,
    apply_migrations,
    open_store,
)
from odoo_sdk.state.runs_store import RunOutcome, RunStore, map_reported_outcome

T0 = 1_760_000_000_000_000  # 2025-10-09T08:53:20Z


@pytest.fixture
def jobs(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    yield store
    store.close()


@pytest.fixture
def runs(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    yield store
    store.close()


def submit(store, job_id="j1", task_id="42", **kw):
    return store.submit(
        job_id=job_id,
        batch_id="b1",
        task_id=task_id,
        repo="o/r",
        brief="do it",
        now_us=kw.pop("now_us", T0),
        **kw,
    )


def pragma(store, name):
    return store._conn.execute(f"PRAGMA {name}").fetchone()[0]


# -- state root ---------------------------------------------------------------


def test_state_root_precedence(tmp_path):
    env = {"ODOO_TASK_TRACKER_DIR": str(tmp_path / "t")}
    with patch.dict(os.environ, env, clear=True):
        assert dispatch_state.state_root() == tmp_path / "t"
        env["ODOO_DISPATCH_STATE_DIR"] = str(tmp_path / "d")
    with patch.dict(os.environ, env, clear=True):
        assert dispatch_state.state_root() == tmp_path / "d"
    with patch.dict(os.environ, {"XDG_STATE_HOME": str(tmp_path)}, clear=True):
        assert dispatch_state.state_root() == tmp_path / "odoo-task-tracker"


def test_state_paths(tmp_path):
    assert dispatch_state.jobs_db_path(tmp_path) == tmp_path / "jobs.db"
    assert dispatch_state.runs_db_path(tmp_path) == tmp_path / "runs.db"
    assert dispatch_state.lock_path(tmp_path) == tmp_path / "dispatcher.lock"
    assert dispatch_state.bundle_dir("j", tmp_path) == tmp_path / "artifacts" / "j"
    assert (
        dispatch_state.friction_path(date(2026, 10, 16), tmp_path)
        == tmp_path / "friction" / "2026-10-16.md"
    )
    with patch.dict(os.environ, {"ODOO_DISPATCH_STATE_DIR": str(tmp_path)}):
        assert dispatch_state.jobs_db_path() == tmp_path / "jobs.db"
    assert dispatch_state.now_us() > T0


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "a\\b"])
def test_bundle_dir_rejects_non_components(bad, tmp_path):
    with pytest.raises(ValueError):
        dispatch_state.bundle_dir(bad, tmp_path)


def test_default_stores_use_state_root(tmp_path):
    with patch.dict(os.environ, {"ODOO_DISPATCH_STATE_DIR": str(tmp_path / "x")}):
        js, rs = JobStore(), RunStore()
    assert (tmp_path / "x" / "jobs.db").exists()
    assert (tmp_path / "x" / "runs.db").exists()
    js.close()
    rs.close()


# -- migrations ---------------------------------------------------------------


def test_migrations_apply_once_and_are_recorded(tmp_path):
    migs = (
        Migration(1, "a", ("CREATE TABLE a (x INTEGER) STRICT",)),
        Migration(2, "b", ("CREATE TABLE b (x INTEGER) STRICT",)),
    )
    conn = open_store(tmp_path / "m.db", migs, synchronous="NORMAL", now_us=T0)
    assert apply_migrations(conn, migs, T0) == []
    rows = conn.execute("SELECT version, name, applied_us FROM schema_migrations")
    assert [tuple(r) for r in rows] == [(1, "a", T0), (2, "b", T0)]
    longer = migs + (Migration(3, "c", ("CREATE TABLE c (x INTEGER) STRICT",)),)
    assert apply_migrations(conn, longer, T0 + 1) == [3]


def test_failed_migration_rolls_back_and_is_not_recorded(tmp_path):
    migs = (Migration(1, "bad", ("CREATE TABLE a (x INTEGER) STRICT", "BOGUS SQL")),)
    with pytest.raises(sqlite3.OperationalError):
        open_store(tmp_path / "m.db", migs, synchronous="NORMAL", now_us=T0)
    conn = sqlite3.connect(tmp_path / "m.db")
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master")]
    assert "a" not in names


def test_migration_already_recorded_by_a_racing_process_is_skipped(tmp_path):
    conn = open_store(tmp_path / "m.db", (), synchronous="NORMAL", now_us=T0)
    conn.execute("INSERT INTO schema_migrations VALUES (1, 'a', 1)")
    from odoo_sdk.state import migrations as m

    # Simulate losing the race: the ledger read before the lock was stale.
    m._apply_one(conn, Migration(1, "a", ("CREATE TABLE a (x INTEGER) STRICT",)), T0)
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master")]
    assert "a" not in names


def test_migration_numbering_must_be_contiguous(tmp_path):
    conn = sqlite3.connect(tmp_path / "m.db", isolation_level=None)
    with pytest.raises(MigrationError):
        apply_migrations(conn, (Migration(2, "x", ()),), T0)


def test_open_store_rejects_bad_synchronous(tmp_path):
    with pytest.raises(ValueError):
        open_store(tmp_path / "m.db", (), synchronous="OFF", now_us=T0)


def test_open_store_asserts_wal(tmp_path):
    real = sqlite3.connect

    class Conn:
        def __init__(self, inner):
            self.inner, self.closed = inner, False

        def execute(self, sql, *a):
            if sql == "PRAGMA journal_mode=WAL":
                return self.inner.execute("SELECT 'delete'")
            return self.inner.execute(sql, *a)

        def close(self):
            self.inner.close()

        @property
        def row_factory(self):
            return None

        @row_factory.setter
        def row_factory(self, value):
            self.inner.row_factory = value

    with patch(
        "odoo_sdk.state.migrations.sqlite3.connect",
        lambda *a, **k: Conn(real(*a, **k)),
    ):
        with pytest.raises(sqlite3.OperationalError, match="not 'wal'"):
            open_store(tmp_path / "m.db", (), synchronous="NORMAL", now_us=T0)


# -- connection settings ------------------------------------------------------


def test_job_store_settings(jobs):
    assert pragma(jobs, "journal_mode") == "wal"
    assert pragma(jobs, "busy_timeout") == 5000
    assert pragma(jobs, "foreign_keys") == 1
    assert pragma(jobs, "synchronous") == 1  # NORMAL


def test_run_store_settings(runs):
    assert pragma(runs, "journal_mode") == "wal"
    assert pragma(runs, "busy_timeout") == 5000
    assert pragma(runs, "foreign_keys") == 1
    assert pragma(runs, "synchronous") == 2  # FULL


# -- job store ----------------------------------------------------------------


def test_job_lifecycle(jobs):
    job = submit(jobs)
    assert job.state is JobState.PENDING and job.started_us is None
    job = jobs.mark_running("j1", container="c", worktree="w", now_us=T0 + 5)
    assert (job.state, job.started_us, job.container) == (JobState.RUNNING, T0 + 5, "c")
    assert jobs.running_task_ids() == {"42"}
    assert jobs.set_session("j1", "s1").session_id == "s1"
    job = jobs.complete("j1", outcome="COMPLETED", now_us=T0 + 9)
    assert (job.state, job.ended_us, job.outcome) == (
        JobState.DONE,
        T0 + 9,
        "COMPLETED",
    )
    assert jobs.list_by_state(JobState.DONE) == [job]
    assert jobs.running_task_ids() == set()


def test_defaults_use_the_clock(jobs):
    job = jobs.submit(job_id="j", batch_id="b", task_id="1", repo="r", brief="x")
    assert job.submitted_us > T0
    assert jobs.mark_running("j").started_us >= job.submitted_us
    assert jobs.complete("j", outcome="FAILED").ended_us is not None
    jobs.submit(job_id="k", batch_id="b", task_id="2", repo="r", brief="x")
    assert jobs.dead_letter("k", DeadReason.KILLED_BY_SIGNAL).ended_us is not None
    assert jobs.reconcile_orphans() == []
    dup = jobs.reject_duplicate(
        job_id="d", batch_id="b", task_id="2", repo="r", brief="x"
    )
    assert dup.state is JobState.DEAD_LETTER


def test_generated_submitted_at_renders_the_integer(jobs):
    job = submit(jobs, now_us=1_700_000_000_123_000)
    assert job.submitted_at == "2023-11-14T22:13:20.123Z"


def test_two_live_jobs_for_one_task_raise(jobs):
    submit(jobs, "j1")
    with pytest.raises(DuplicateTaskInFlightError):
        submit(jobs, "j2")
    jobs.mark_running("j1", now_us=T0 + 1)
    with pytest.raises(DuplicateTaskInFlightError):
        submit(jobs, "j3")
    jobs.complete("j1", outcome="COMPLETED", now_us=T0 + 2)
    assert submit(jobs, "j4").state is JobState.PENDING  # re-dispatch works


def test_the_engine_enforces_the_gate_not_the_store(jobs):
    submit(jobs, "j1")
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        jobs._conn.execute(
            "INSERT INTO jobs (job_id, batch_id, task_id, repo, state, brief, "
            "submitted_us) VALUES ('x','b','42','r','PENDING','b',1)"
        )


def test_concurrent_submissions_one_winner(tmp_path):
    path = tmp_path / "jobs.db"
    stores = [JobStore(path) for _ in range(6)]
    results = []

    def go(i):
        try:
            stores[i].submit(
                job_id=f"j{i}", batch_id="b", task_id="9", repo="r", brief="x"
            )
            results.append("won")
        except DuplicateTaskInFlightError:
            results.append("lost")

    threads = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["lost"] * 5 + ["won"]
    for s in stores:
        s.close()


def test_other_integrity_errors_propagate(jobs):
    submit(jobs, "j1")
    with pytest.raises(sqlite3.IntegrityError):
        submit(jobs, "j1", task_id="other")  # primary key
    with pytest.raises(sqlite3.IntegrityError):
        jobs.submit(job_id="j9", batch_id="b", task_id="", repo="r", brief="x")


def test_dead_letter_and_reconcile(jobs):
    submit(jobs, "j1", task_id="1")
    submit(jobs, "j2", task_id="2")
    submit(jobs, "j3", task_id="3")
    jobs.mark_running("j1", now_us=T0 + 1)
    jobs.mark_running("j2", now_us=T0 + 1)
    dead = jobs.dead_letter("j3", DeadReason.CONTAINER_START_FAILED, detail="rc=1")
    assert (dead.dead_reason, dead.detail) == ("CONTAINER_START_FAILED", "rc=1")
    assert jobs.reconcile_orphans(detail="restart", now_us=T0 + 3) == ["j1", "j2"]
    reasons = {j.job_id: j.dead_reason for j in jobs.dead_letters()}
    assert reasons == {
        "j1": "ORPHANED_BY_RESTART",
        "j2": "ORPHANED_BY_RESTART",
        "j3": "CONTAINER_START_FAILED",
    }
    assert jobs.count_live("o/r") == 0


def test_count_live_counts_pending_and_running(jobs):
    submit(jobs, "j1", task_id="1")
    submit(jobs, "j2", task_id="2")
    jobs.mark_running("j2", now_us=T0 + 1)
    assert jobs.count_live("o/r") == 2
    assert jobs.count_live("other") == 0


def test_reject_duplicate_row(jobs):
    submit(jobs)
    row = jobs.reject_duplicate(
        job_id="j2", batch_id="b", task_id="42", repo="o/r", brief="x", detail="dup"
    )
    assert row.dead_reason == "DUPLICATE_TASK_IN_FLIGHT"
    assert row.started_us is None and row.ended_us is not None


@pytest.mark.parametrize(
    "op",
    [
        lambda s: s.mark_running("nope"),
        lambda s: s.set_session("j1", "s"),  # PENDING
        lambda s: s.complete("j1", outcome="COMPLETED"),  # PENDING
        lambda s: s.dead_letter("nope", DeadReason.KILLED_BY_DEADLINE),
    ],
)
def test_invalid_transitions(jobs, op):
    submit(jobs)
    with pytest.raises(InvalidJobTransitionError):
        op(jobs)


def test_cannot_start_or_dead_letter_a_finished_job(jobs):
    submit(jobs)
    jobs.dead_letter("j1", DeadReason.KILLED_BY_DEADLINE)
    with pytest.raises(InvalidJobTransitionError):
        jobs.mark_running("j1")
    with pytest.raises(InvalidJobTransitionError):
        jobs.dead_letter("j1", DeadReason.KILLED_BY_SIGNAL)


def test_get_missing_job(jobs):
    with pytest.raises(KeyError):
        jobs.get("nope")


def test_dead_reason_enum_matches_the_ten_causes():
    assert len(DeadReason) == 10
    assert all(r.name == r.value for r in DeadReason)


_INSERT_JOB = (
    "INSERT INTO jobs (job_id, batch_id, task_id, repo, state, brief, submitted_us, "
    "started_us, ended_us, dead_reason) VALUES ('x','b','1','r',?,'b',100,?,?,?)"
)


@pytest.mark.parametrize(
    "args",
    [
        ("PENDING", 100, None, ""),  # PENDING with a start
        ("RUNNING", None, None, ""),  # RUNNING without a start
        ("RUNNING", 100, 200, ""),  # RUNNING with an end
        ("DONE", 100, None, ""),  # DONE without an end
        ("DEAD_LETTER", None, 200, ""),  # dead without a reason
        ("DONE", 100, 200, "KILLED_BY_SIGNAL"),  # reason without DEAD_LETTER
        ("DEAD_LETTER", None, None, "KILLED_BY_SIGNAL"),  # dead without an end
        ("DEAD_LETTER", None, 200, "MADE_UP_CAUSE"),  # unknown cause
        ("DONE", 50, 200, ""),  # started before submitted
        ("DONE", 150, 120, ""),  # ended before started
        ("BOGUS", None, None, ""),  # unknown state
    ],
)
def test_cross_field_checks_reject(jobs, args):
    with pytest.raises(sqlite3.IntegrityError):
        jobs._conn.execute(_INSERT_JOB, args)


@pytest.mark.parametrize(
    "args",
    [
        ("PENDING", None, None, ""),
        ("RUNNING", 100, None, ""),
        ("DONE", 100, 200, ""),
        ("DEAD_LETTER", None, 200, "KILLED_BY_SIGNAL"),
    ],
)
def test_cross_field_checks_accept_valid_rows(jobs, args):
    jobs._conn.execute(_INSERT_JOB, args)


def test_jobs_table_is_strict(jobs):
    with pytest.raises(sqlite3.IntegrityError):
        jobs._conn.execute(
            "INSERT INTO jobs (job_id, batch_id, task_id, repo, state, brief, "
            "submitted_us) VALUES ('x','b','1','r','PENDING','b','not a number')"
        )


# -- outcome mapping ----------------------------------------------------------


@pytest.mark.parametrize(
    "reported,questions,expected",
    [
        ("done", 0, RunOutcome.COMPLETED),
        ("done", 2, RunOutcome.COMPLETED_WITH_QUESTIONS),
        ("partial", 0, RunOutcome.COMPLETED_WITH_QUESTIONS),
        ("partial", 3, RunOutcome.COMPLETED_WITH_QUESTIONS),
        ("blocked", 0, RunOutcome.BLOCKED),
        ("blocked", 1, RunOutcome.BLOCKED),
        (None, 0, RunOutcome.FAILED),
        ("", 0, RunOutcome.FAILED),
        ("DONE", 0, RunOutcome.FAILED),
        ("something else", 5, RunOutcome.FAILED),
    ],
)
def test_outcome_mapping_is_total(reported, questions, expected):
    assert map_reported_outcome(reported, questions) is expected


def test_every_mapped_outcome_is_storable(runs):
    for i, reported in enumerate(["done", "partial", "blocked", "junk", None]):
        outcome = map_reported_outcome(reported)
        assert runs.record_run(
            session_id=f"s{i}",
            job_id="j",
            task_id="1",
            started_us=T0,
            ended_us=T0,
            outcome=outcome,
            tool_version="1",
        )


# -- run store ----------------------------------------------------------------


def record(runs, session_id="s1", **kw):
    args = dict(
        session_id=session_id,
        job_id="j1",
        task_id="42",
        started_us=T0,
        ended_us=T0 + 2_000_000,
        outcome=RunOutcome.COMPLETED,
        tool_version="2.1.0",
        repo="o/r",
        branch="b",
        pr_num=7,
        transcript="/t.jsonl",
        brief="the ask",
        envelope={"result": "ok", "usage": {"input_tokens": 1}},
    )
    args.update(kw)
    return runs.record_run(**args)


def test_record_and_read_run(runs):
    assert record(runs)
    run = runs.get_run("s1")
    assert (run.task_id, run.pr_num, run.brief, run.outcome) == (
        "42",
        7,
        "the ask",
        RunOutcome.COMPLETED,
    )
    assert runs.get_envelope("s1") == {"result": "ok", "usage": {"input_tokens": 1}}
    assert runs.iter_runs() == [run]


def test_generated_time_columns_render_the_integers(runs):
    record(runs, started_us=1_700_000_000_123_456, ended_us=1_700_000_001_000_000)
    run = runs.get_run("s1")
    assert run.started_at == "2023-11-14T22:13:20.123Z"
    assert run.ended_at == "2023-11-14T22:13:21.000Z"


def test_artifact_generated_time_column(runs):
    runs.record_artifacts(job_id="j", dir="/d", harvested_us=1_700_000_000_000_000)
    assert runs.get_artifacts("j").harvested_at == "2023-11-14T22:13:20.000Z"


def test_record_run_is_idempotent_on_session(runs):
    assert record(runs)
    assert record(runs, ended_us=T0 + 9_000_000) is False
    assert runs.get_run("s1").ended_us == T0 + 2_000_000


def test_failed_run_without_envelope_or_description(runs):
    assert record(runs, outcome=RunOutcome.FAILED, envelope=None, brief="b")
    assert runs.get_envelope("s1") is None
    assert runs.get_run("s1").outcome is RunOutcome.FAILED


def test_envelope_accepts_a_json_string(runs):
    record(runs, envelope='{"a": 1}')
    assert runs.get_envelope("s1") == {"a": 1}


def test_run_validation(runs):
    with pytest.raises(ValueError):
        record(runs, session_id="")
    with pytest.raises(sqlite3.IntegrityError):
        record(runs, session_id="s2", ended_us=T0 - 1)
    with pytest.raises(sqlite3.IntegrityError):
        record(runs, session_id="s3", task_id="")
    with pytest.raises(sqlite3.IntegrityError):
        record(runs, session_id="s4", tool_version="")
    with pytest.raises(sqlite3.IntegrityError):
        record(runs, session_id="s5", envelope="not json")
    with pytest.raises(ValueError):
        record(runs, session_id="s6", outcome="done")
    assert runs.iter_runs() == []  # every failure rolled the whole write back


def test_job_id_is_not_a_foreign_key(runs):
    assert record(runs, job_id="a-job-that-exists-in-no-store")


def test_get_run_missing(runs):
    with pytest.raises(KeyError):
        runs.get_run("nope")


def test_iter_runs_window(runs):
    for i, (a, b) in enumerate([(10, 20), (30, 40), (50, 60)]):
        record(runs, session_id=f"s{i}", started_us=a, ended_us=b)
    ids = lambda **kw: [r.session_id for r in runs.iter_runs(**kw)]
    assert ids() == ["s0", "s1", "s2"]
    assert ids(start_us=40) == ["s1", "s2"]
    assert ids(end_us=30) == ["s0"]
    assert ids(start_us=25, end_us=55) == ["s1", "s2"]


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE runs SET repo='x'",
        "DELETE FROM runs",
        "UPDATE run_envelopes SET envelope='{}'",
        "DELETE FROM run_envelopes",
        "UPDATE run_artifacts SET bytes=1",
        "DELETE FROM run_artifacts",
    ],
)
def test_append_only_triggers(runs, sql):
    record(runs)
    runs.record_artifacts(job_id="j1", dir="/d", harvested_us=T0)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        runs._conn.execute(sql)


def test_run_envelope_requires_a_run(runs):
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        runs._conn.execute(
            "INSERT INTO run_envelopes (session_id, envelope) VALUES ('ghost','{}')"
        )


def test_record_artifacts_flags_and_duplicates(runs):
    assert runs.record_artifacts(
        job_id="j",
        dir="/d",
        harvested_us=T0,
        has_task_json=True,
        bytes=5,
        missing="x",
    )
    assert runs.record_artifacts(job_id="j", dir="/d", harvested_us=T0) is False
    index = runs.get_artifacts("j")
    assert (index.has_task_json, index.has_session, index.bytes) == (True, False, 5)
    assert index.session_id == ""
    assert runs.get_artifacts("nope") is None
    with pytest.raises(sqlite3.IntegrityError):
        runs.record_artifacts(job_id="k", dir="", harvested_us=T0)


# -- artifacts ----------------------------------------------------------------


def test_bundle_layout_names():
    assert BUNDLE_FILES == (
        "task.json",
        "session.jsonl",
        "envelope.json",
        "harvest.json",
    )


def test_ensure_bundle_dir(tmp_path):
    path = ensure_bundle_dir("j1", tmp_path)
    assert path == tmp_path / "artifacts" / "j1" and path.is_dir()
    assert ensure_bundle_dir("j1", tmp_path) == path


def test_collect_full_bundle(tmp_path, runs):
    bundle = ensure_bundle_dir("j1", tmp_path)
    (bundle / "task.json").write_text('{"spec": 1}')
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("line\n")
    h = collect_artifacts(
        "j1",
        session_id="s1",
        transcript=transcript,
        envelope='{"r": 1}',
        runs=runs,
        root=tmp_path,
        now_us=T0,
    )
    assert (h.has_task_json, h.has_session, h.has_envelope) == (True, True, True)
    assert h.missing == () and h.bytes == len('{"spec": 1}') + 5 + len('{"r": 1}')
    assert (bundle / "session.jsonl").read_text() == "line\n"
    assert transcript.exists()  # copied, not moved
    harvest = json.loads((bundle / "harvest.json").read_text())
    assert harvest["missing"] == [] and harvest["session_id"] == "s1"
    assert not list(bundle.glob("*.tmp"))
    index = runs.get_artifacts("j1")
    assert index.dir == str(bundle) and index.has_session and index.missing == ""


def test_collect_for_a_job_that_never_had_a_session(tmp_path, runs):
    h = collect_artifacts("j2", runs=runs, root=tmp_path, now_us=T0)
    assert (h.has_task_json, h.has_session, h.has_envelope, h.bytes) == (
        False,
        False,
        False,
        0,
    )
    assert len(h.missing) == 3  # session, envelope, task.json
    index = runs.get_artifacts("j2")
    assert index.session_id == "" and "task.json" in index.missing
    assert (tmp_path / "artifacts" / "j2" / "harvest.json").exists()


def test_missing_transcript_file_is_recorded(tmp_path):
    h = collect_artifacts(
        "j1", transcript=tmp_path / "gone.jsonl", root=tmp_path, now_us=T0
    )
    assert not h.has_session
    assert any("does not exist" in line for line in h.missing)


def test_half_written_task_json_is_recorded_not_raised(tmp_path):
    bundle = ensure_bundle_dir("j1", tmp_path)
    (bundle / "task.json").write_text('{"spec": ')
    h = collect_artifacts("j1", root=tmp_path, now_us=T0)
    assert not h.has_task_json
    assert any("not valid JSON" in line for line in h.missing)


def test_unreadable_task_json_is_recorded(tmp_path):
    bundle = ensure_bundle_dir("j1", tmp_path)
    (bundle / "task.json").write_text("{}")
    with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
        h = collect_artifacts("j1", root=tmp_path, now_us=T0)
    assert any("unreadable" in line for line in h.missing)


def test_collection_failures_never_raise_and_never_touch_the_run(tmp_path, runs):
    record(runs, job_id="j1")
    before = runs.get_run("s1")
    blocker = tmp_path / "artifacts"
    blocker.write_text("a file where the bundle root should be")
    h = collect_artifacts(
        "j1",
        session_id="s1",
        transcript=tmp_path / "x",
        envelope="{}",
        runs=runs,
        root=tmp_path,
        now_us=T0,
    )
    assert not (h.has_task_json or h.has_session or h.has_envelope)
    assert any(line.startswith("bundle directory") for line in h.missing)
    assert "harvest.json:" in runs.get_artifacts("j1").missing
    assert runs.get_run("s1") == before  # the run row is untouched
    assert runs.get_artifacts("j1").bytes == 0


def test_index_failure_is_swallowed(tmp_path):
    class Broken:
        def record_artifacts(self, **kw):
            raise sqlite3.OperationalError("disk full")

    h = collect_artifacts("j1", runs=Broken(), root=tmp_path, now_us=T0)
    assert h.job_id == "j1"


def test_envelope_write_failure_is_recorded(tmp_path):
    ensure_bundle_dir("j1", tmp_path)
    with patch(
        "odoo_sdk.state.artifacts._replace_atomically", side_effect=OSError("full")
    ):
        h = collect_artifacts("j1", envelope="{}", root=tmp_path, now_us=T0)
    assert not h.has_envelope
    assert any("envelope.json: OSError" in line for line in h.missing)


def test_collect_default_clock(tmp_path):
    h = collect_artifacts("j1", root=tmp_path)
    assert h.harvested_us > T0
