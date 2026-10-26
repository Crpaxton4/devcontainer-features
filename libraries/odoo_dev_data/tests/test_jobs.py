"""Tests for ``JobStore`` on ``state.db``: lifecycle, the duplicate gate, the
absorbed run, bundle and orchestrator columns, and the report/window queries."""

import re
import sqlite3
import threading
from pathlib import Path

import pytest

from odoo_dev_data.db import migrations
from odoo_dev_data.jobs import (
    DeadReason,
    DuplicateTaskInFlightError,
    InvalidJobTransitionError,
    Job,
    JobState,
    JobStore,
    RunOutcome,
    map_reported_outcome,
)

T0 = 1_760_000_000_000_000  # 2025-10-09T08:53:20Z


@pytest.fixture
def jobs(tmp_path):
    with JobStore(home=tmp_path) as store:
        yield store


def submit(store, job_id="j1", task_id="42", **kw):
    return store.submit(
        job_id=job_id,
        batch_id=kw.pop("batch_id", "b1"),
        task_id=task_id,
        repo="o/r",
        brief="do it",
        now_us=kw.pop("now_us", T0),
        **kw,
    )


def run_to_done(store, job_id="j1", task_id="42", at=T0, **kw):
    submit(store, job_id, task_id, now_us=at, **kw)
    store.mark_running(job_id, now_us=at + 1)
    return store.record_result(job_id, outcome=RunOutcome.COMPLETED, now_us=at + 2)


def pragma(store, name):
    return store._conn.execute(f"PRAGMA {name}").fetchone()[0]


def _row(job_id, task_id, **kw):
    return dict(job_id=job_id, batch_id="B", task_id=task_id, repo="r", brief="x", **kw)


# -- connection ---------------------------------------------------------------


def test_job_store_settings(jobs):
    assert pragma(jobs, "journal_mode") == "wal"
    assert pragma(jobs, "busy_timeout") == 5000
    assert pragma(jobs, "foreign_keys") == 1
    assert pragma(jobs, "recursive_triggers") == 1


def test_store_can_share_a_connection(tmp_path):
    from odoo_dev_data.db.engine import connect

    conn = connect(tmp_path)
    store = JobStore(conn)
    assert store.submit(
        job_id="j", batch_id="b", task_id="1", repo="r", brief="x"
    ).state is (JobState.PENDING)
    store.close()


# -- lifecycle ----------------------------------------------------------------


def test_job_lifecycle(jobs):
    job = submit(jobs)
    assert job.state is JobState.PENDING and job.started_us is None
    job = jobs.mark_running("j1", container="c", worktree="w", now_us=T0 + 5)
    assert (job.state, job.started_us, job.container) == (JobState.RUNNING, T0 + 5, "c")
    assert jobs.running_task_ids() == {"42"}
    assert jobs.set_session("j1", "s1").session_id == "s1"
    job = jobs.record_result("j1", outcome="COMPLETED", now_us=T0 + 9)
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
    assert jobs.record_result("j", outcome="FAILED").ended_us is not None
    jobs.submit(job_id="k", batch_id="b", task_id="2", repo="r", brief="x")
    assert jobs.dead_letter("k", DeadReason.KILLED_BY_SIGNAL).ended_us is not None
    assert jobs.reconcile_orphans() == []
    dup = jobs.reject_duplicate(
        job_id="d", batch_id="b", task_id="2", repo="r", brief="x"
    )
    assert dup.state is JobState.DEAD_LETTER


def test_entry_round_trips_and_defaults_to_empty(jobs):
    assert submit(jobs, "j1", task_id="1").entry == ""
    job = submit(jobs, "j2", task_id="2", entry="/odoo-dev:upgrade-implement")
    assert jobs.get("j2").entry == job.entry == "/odoo-dev:upgrade-implement"
    rows = [_row("j3", "3"), _row("j4", "4", entry="/odoo-dev:implement")]
    assert [j.entry for j in jobs.submit_all(rows)] == ["", "/odoo-dev:implement"]
    dup = jobs.reject_duplicate(
        job_id="d", batch_id="b", task_id="2", repo="r", brief="x", entry="/e"
    )
    assert dup.entry == "/e"


def test_approved_us_is_stored_on_submit(jobs):
    assert submit(jobs, "j1", task_id="1").approved_us is None
    assert submit(jobs, "j2", task_id="2", approved_us=T0 + 3).approved_us == T0 + 3
    rows = [_row("j3", "3", approved_us=T0 + 4), _row("j4", "4")]
    assert [j.approved_us for j in jobs.submit_all(rows)] == [T0 + 4, None]


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
    jobs.record_result("j1", outcome="COMPLETED", now_us=T0 + 2)
    assert submit(jobs, "j4").state is JobState.PENDING  # re-dispatch works


def test_the_engine_enforces_the_gate_not_the_store(jobs):
    submit(jobs, "j1")
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        jobs._conn.execute(
            "INSERT INTO jobs (job_id, batch_id, task_id, repo, state, brief, "
            "submitted_us) VALUES ('x','b','42','r','PENDING','b',1)"
        )


def test_concurrent_submissions_one_winner(tmp_path):
    stores = [JobStore(home=tmp_path) for _ in range(6)]
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


def test_job_id_clash_is_not_a_duplicate_task(jobs):
    submit(jobs, "j1", task_id="1")
    with pytest.raises(sqlite3.IntegrityError) as caught:
        submit(jobs, "j1", task_id="2")
    assert not isinstance(caught.value, DuplicateTaskInFlightError)
    assert caught.value.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY


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


def test_submit_all_inserts_in_order_and_list_by_batch_reads_them(jobs):
    done = jobs.submit_all([_row("z", "1"), _row("a", "2")], now_us=T0)
    assert [j.job_id for j in done] == ["z", "a"]
    assert [j.job_id for j in jobs.list_by_batch("B")] == ["z", "a"]
    assert jobs.list_by_batch("unknown") == []
    assert jobs.submit_all([]) == []


def test_submit_all_rolls_back_on_a_live_twin(jobs):
    submit(jobs, "live", task_id="2")
    with pytest.raises(DuplicateTaskInFlightError):
        jobs.submit_all([_row("n", "1"), _row("m", "2")])
    assert jobs.list_by_batch("B") == []
    with pytest.raises(DuplicateTaskInFlightError):
        jobs.submit_all([_row("p", "7"), _row("q", "7")])
    assert jobs.list_by_batch("B") == []


def test_submit_all_lets_a_job_id_clash_propagate(jobs):
    jobs.submit_all([_row("n", "1")])
    with pytest.raises(sqlite3.IntegrityError):
        jobs.submit_all([_row("n", "2")])


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
        lambda s: s.record_result("j1", outcome="COMPLETED"),  # PENDING
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
    text = Path(migrations.__file__).read_text()
    check = re.search(
        r"dead_reason\s+TEXT.*?CHECK \(dead_reason IN \((.*?)\)\)", text, re.S
    )
    reasons = re.findall(r"'([A-Z_]+)'", check.group(1))
    assert reasons and {r.value for r in DeadReason} == set(reasons)


# -- schema checks ------------------------------------------------------------

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


@pytest.mark.parametrize("count", [None, "2", 2.5, [1], b"1", True, False])
def test_outcome_mapping_survives_a_non_int_question_count(count):
    assert map_reported_outcome("done", count) is RunOutcome.COMPLETED
    assert map_reported_outcome("blocked", count) is RunOutcome.BLOCKED


def test_every_mapped_outcome_is_storable(jobs):
    for i, reported in enumerate(["done", "partial", "blocked", "junk", None]):
        job_id = f"j{i}"
        submit(jobs, job_id, task_id=str(i))
        jobs.mark_running(job_id, now_us=T0 + 1)
        done = jobs.record_result(job_id, outcome=map_reported_outcome(reported))
        assert done.outcome in {o.value for o in RunOutcome}


# -- record_result ------------------------------------------------------------


def test_record_result_stores_every_value(jobs):
    submit(jobs)
    jobs.mark_running("j1", now_us=T0 + 1)
    jobs.set_session("j1", "s1")
    job = jobs.record_result(
        "j1",
        outcome=RunOutcome.COMPLETED_WITH_QUESTIONS,
        branch="b/1",
        pr_num=7,
        tool_version="1.2",
        transcript_path="/t.jsonl",
        input_tokens=10,
        output_tokens=20,
        cost_usd=1.5,
        question_count=3,
        now_us=T0 + 9,
    )
    assert (job.state, job.outcome) == (JobState.DONE, "COMPLETED_WITH_QUESTIONS")
    assert (job.branch, job.pr_num, job.tool_version) == ("b/1", 7, "1.2")
    assert (job.transcript_path, job.input_tokens, job.output_tokens) == (
        "/t.jsonl",
        10,
        20,
    )
    assert (job.cost_usd, job.question_count, job.session_id) == (1.5, 3, "s1")
    assert job.envelope == "" and job.envelope_json() is None


def test_record_result_keeps_session_and_question_count_when_not_given(jobs):
    submit(jobs)
    jobs.mark_running("j1", now_us=T0 + 1)
    jobs.set_session("j1", "s1")
    jobs.increment_questions("j1")
    job = jobs.record_result("j1", outcome="COMPLETED")
    assert (job.session_id, job.question_count) == ("s1", 1)
    other = submit(jobs, "j2", task_id="2")
    jobs.mark_running(other.job_id, now_us=T0 + 1)
    assert (
        jobs.record_result("j2", outcome="COMPLETED", session_id="s2").session_id
        == "s2"
    )


def test_record_result_stores_a_dict_envelope_as_json(jobs):
    submit(jobs)
    jobs.mark_running("j1", now_us=T0 + 1)
    job = jobs.record_result("j1", outcome="COMPLETED", envelope={"a": [1, 2]})
    assert job.envelope == '{"a": [1, 2]}'
    assert job.envelope_json() == {"a": [1, 2]}
    assert jobs.get("j1").envelope_json() == {"a": [1, 2]}


def test_record_result_accepts_a_string_envelope(jobs):
    submit(jobs)
    jobs.mark_running("j1", now_us=T0 + 1)
    assert jobs.record_result(
        "j1", outcome="FAILED", envelope='{"k": 1}'
    ).envelope_json() == {"k": 1}


def test_record_result_requires_a_running_job(jobs):
    submit(jobs)
    with pytest.raises(InvalidJobTransitionError):
        jobs.record_result("j1", outcome="COMPLETED")  # PENDING
    jobs.mark_running("j1", now_us=T0 + 1)
    jobs.record_result("j1", outcome="COMPLETED")
    with pytest.raises(InvalidJobTransitionError):
        jobs.record_result("j1", outcome="COMPLETED")  # DONE
    with pytest.raises(InvalidJobTransitionError):
        jobs.record_result("nope", outcome="COMPLETED")


def test_record_result_rejects_an_unknown_outcome(jobs):
    submit(jobs)
    jobs.mark_running("j1", now_us=T0 + 1)
    with pytest.raises(ValueError):
        jobs.record_result("j1", outcome="WONDERFUL")
    assert jobs.get("j1").state is JobState.RUNNING


def test_billable_window(jobs):
    assert submit(jobs).billable_window is None
    running = jobs.mark_running("j1", now_us=T0 + 1)
    assert running.billable_window is None
    done = jobs.record_result("j1", outcome="COMPLETED", now_us=T0 + 4)
    assert done.billable_window == (T0 + 1, T0 + 4)
    assert isinstance(done, Job)


# -- index_bundle -------------------------------------------------------------


def test_index_bundle_on_a_done_job_sets_harvested_us(jobs):
    run_to_done(jobs)
    job = jobs.index_bundle(
        "j1",
        bundle_dir="/b/j1",
        has_task_json=True,
        has_envelope=True,
        bytes=123,
        missing="session.jsonl",
        now_us=T0 + 50,
    )
    assert (job.bundle_dir, job.harvested_us, job.bytes) == ("/b/j1", T0 + 50, 123)
    assert (job.has_task_json, job.has_session, job.has_envelope) == (True, False, True)
    assert job.missing == "session.jsonl"


def test_index_bundle_on_a_dead_letter_job(jobs):
    submit(jobs)
    jobs.dead_letter("j1", DeadReason.KILLED_BY_SIGNAL)
    assert jobs.index_bundle("j1", bundle_dir="/b").harvested_us is not None


def test_index_bundle_rejects_live_jobs(jobs):
    submit(jobs)
    with pytest.raises(InvalidJobTransitionError):
        jobs.index_bundle("j1", bundle_dir="/b")  # PENDING
    jobs.mark_running("j1", now_us=T0 + 1)
    with pytest.raises(InvalidJobTransitionError):
        jobs.index_bundle("j1", bundle_dir="/b")  # RUNNING
    assert jobs.get("j1").harvested_us is None


# -- orchestrator marks -------------------------------------------------------


def test_approve_brief_on_pending_and_running(jobs):
    submit(jobs, "j1", task_id="1")
    assert jobs.approve_brief("j1", now_us=T0 + 2).approved_us == T0 + 2
    submit(jobs, "j2", task_id="2")
    jobs.mark_running("j2", now_us=T0 + 1)
    assert jobs.approve_brief("j2").approved_us is not None
    jobs.record_result("j2", outcome="COMPLETED")
    with pytest.raises(InvalidJobTransitionError):
        jobs.approve_brief("j2")


def test_request_stop_sets_stop_requested_us(jobs):
    submit(jobs)
    with pytest.raises(InvalidJobTransitionError):
        jobs.request_stop("j1")  # PENDING
    jobs.mark_running("j1", now_us=T0 + 1)
    assert jobs.request_stop("j1", now_us=T0 + 3).stop_requested_us == T0 + 3


def test_increment_questions_counts(jobs):
    submit(jobs)
    with pytest.raises(InvalidJobTransitionError):
        jobs.increment_questions("j1")  # PENDING
    jobs.mark_running("j1", now_us=T0 + 1)
    jobs.increment_questions("j1")
    assert jobs.increment_questions("j1").question_count == 2


def test_mark_reported_is_idempotent(jobs):
    run_to_done(jobs, "j1", "1")
    run_to_done(jobs, "j2", "2")
    assert jobs.mark_reported(["j1", "j2", "nope"], now_us=T0 + 10) == 2
    assert jobs.mark_reported(["j1", "j2"], now_us=T0 + 11) == 0
    assert jobs.get("j1").reported_us == T0 + 10
    assert jobs.mark_reported([]) == 0


def test_mark_friction_recorded_is_idempotent(jobs):
    run_to_done(jobs)
    assert jobs.mark_friction_recorded(["j1"], now_us=T0 + 10) == 1
    assert jobs.mark_friction_recorded(["j1"]) == 0
    assert jobs.get("j1").friction_recorded_us == T0 + 10


def test_list_batches_pending_report(jobs):
    run_to_done(jobs, "a1", "1", batch_id="A", at=T0 + 30)
    run_to_done(jobs, "a2", "2", batch_id="A", at=T0 + 30)
    run_to_done(jobs, "b1", "3", batch_id="B", at=T0 + 10)
    submit(jobs, "c1", "4", batch_id="C", now_us=T0)  # PENDING batch
    run_to_done(jobs, "c2", "5", batch_id="C", at=T0)
    run_to_done(jobs, "d1", "6", batch_id="D", at=T0 + 5)
    jobs.mark_reported(["d1"])
    assert jobs.list_batches_pending_report() == ["B", "A"]
    jobs.mark_reported(["a1"])  # one unreported job keeps the batch pending
    assert jobs.list_batches_pending_report() == ["B", "A"]
    jobs.mark_reported(["a2", "b1"])
    assert jobs.list_batches_pending_report() == []


def test_batch_with_a_running_job_is_not_pending_report(jobs):
    run_to_done(jobs, "a1", "1", batch_id="A")
    submit(jobs, "a2", "2", batch_id="A")
    jobs.mark_running("a2", now_us=T0 + 1)
    assert jobs.list_batches_pending_report() == []


def test_dead_lettered_jobs_count_as_finished_for_the_report(jobs):
    submit(jobs, "a1", "1", batch_id="A")
    jobs.dead_letter("a1", DeadReason.CONTAINER_START_FAILED)
    assert jobs.list_batches_pending_report() == ["A"]


# -- list_finished ------------------------------------------------------------


def _ids(jobs, **kw):
    return [j.job_id for j in jobs.list_finished(**kw)]


def _finished(jobs, job_id, start, end, task_id=None):
    submit(jobs, job_id, task_id or job_id, now_us=1)
    jobs.mark_running(job_id, now_us=start)
    jobs.record_result(job_id, outcome="COMPLETED", now_us=end)


def test_list_finished_skips_unstarted_and_unfinished_jobs(jobs):
    submit(jobs, "never", "1")
    jobs.dead_letter("never", DeadReason.CONTAINER_START_FAILED)  # no start
    submit(jobs, "live", "2")
    jobs.mark_running("live", now_us=T0 + 1)
    _finished(jobs, "done", T0 + 2, T0 + 3, "3")
    assert _ids(jobs) == ["done"]


def test_adjacent_windows_never_both_return_a_job(jobs):
    _finished(jobs, "a", 10, 20)
    _finished(jobs, "z", 15, 15)
    assert _ids(jobs, start_us=10, end_us=20) == ["a", "z"]
    assert _ids(jobs, start_us=20, end_us=30) == []
    assert _ids(jobs, start_us=15, end_us=16) == ["a", "z"]  # zero-length is in
    assert _ids(jobs, start_us=19, end_us=20) == ["a"]


def test_every_job_is_in_exactly_one_partitioning_window(jobs):
    spans = [(10, 20), (20, 30), (1, 100), (5, 10), (15, 15), (20, 20), (10, 10)]
    for i, (a, b) in enumerate(spans):
        _finished(jobs, f"s{i}", a, b)
    edges = [0, 10, 15, 20, 25, 30, 100, 101]
    for i, (a, b) in enumerate(spans):
        windows = [
            (lo, hi)
            for lo, hi in zip(edges, edges[1:])
            if f"s{i}" in _ids(jobs, start_us=lo, end_us=hi)
        ]
        if a == b:
            assert len(windows) == 1, (a, b, windows)
        else:  # a span job appears in each window it overlaps, never none
            assert windows, (a, b)


def test_list_finished_orders_by_start_then_job_id(jobs):
    _finished(jobs, "b", 10, 20)
    _finished(jobs, "a", 10, 20)
    _finished(jobs, "c", 5, 6)
    assert _ids(jobs) == ["c", "a", "b"]
