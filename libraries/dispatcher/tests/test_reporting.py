"""The read-only reporting API."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from odoo_dev_data.jobs import DeadReason, JobState, JobStore

from dispatcher import queue, reporting
from .support import GOOD


@pytest.fixture
def seeded(home):
    """Batch ``b1``: one DONE job with an envelope, one dead letter, one PENDING."""
    with JobStore(home=home) as store:
        done, dead, waiting = (
            store.submit(
                job_id=f"job{task}",
                batch_id="b1",
                task_id=task,
                repo="a",
                brief="x",
                now_us=500_000,
            )
            for task in ("1", "2", "3")
        )
        store.mark_running(done.job_id, now_us=1_000_000)
        envelope = {"type": "result", "structured_output": GOOD}
        store.record_result(
            done.job_id,
            outcome="COMPLETED_WITH_QUESTIONS",
            session_id="s1",
            branch="feat",
            pr_num=5,
            envelope=envelope,
            input_tokens=10,
            output_tokens=20,
            cost_usd=1.5,
            question_count=2,
            now_us=3_000_000,
        )
        store.index_bundle(done.job_id, bundle_dir="/b", has_envelope=True)
        store.mark_running(dead.job_id, now_us=1_000_000)
        store.dead_letter(
            dead.job_id,
            DeadReason.SESSION_EXIT_NONZERO,
            detail="boom",
            now_us=2_000_000,
        )
    return done, dead, waiting


def test_batch_report_shape_and_totals(home, seeded):
    done, dead, waiting = seeded
    report = reporting.batch_report(home, "b1")
    assert list(report) == ["batch_id", "jobs", "totals"]
    first, second, third = report["jobs"]
    assert set(first) == {
        "job_id", "task", "state", "outcome", "dead_reason", "detail",
        "started_at", "ended_at", "duration_s", "branch", "pr_num", "bundle",
        "question_count", "input_tokens", "output_tokens", "cost_usd",
        "structured", "reported",
    }  # fmt: skip
    assert first["job_id"] == done.job_id and first["task"] == "1"
    assert (first["state"], first["outcome"]) == ("DONE", "COMPLETED_WITH_QUESTIONS")
    assert first["duration_s"] == 2.0 and first["bundle"] == "/b"
    assert first["structured"]["outcome"] == "done" and first["reported"] is False
    assert (first["branch"], first["pr_num"], first["question_count"]) == ("feat", 5, 2)
    assert (first["input_tokens"], first["output_tokens"], first["cost_usd"]) == (
        10,
        20,
        1.5,
    )
    assert first["started_at"].startswith("1970-01-01T00:00:01")
    assert (
        second["dead_reason"] == "SESSION_EXIT_NONZERO" and second["detail"] == "boom"
    )
    assert second["structured"] is None and second["bundle"] is None
    assert third["state"] == "PENDING" and third["started_at"] is None
    assert third["duration_s"] is None
    assert report["totals"] == {
        "jobs": 3, "done": 1, "dead": 1, "cost_usd": 1.5, "questions": 2,
    }  # fmt: skip
    json.dumps(report)  # the orchestrator consumes it as JSON
    empty = reporting.batch_report(home, "nope")
    assert empty["jobs"] == [] and empty["totals"]["jobs"] == 0


def test_queue_dead_letters_and_filters(home, seeded):
    done, dead, waiting = seeded
    assert [j.job_id for j in reporting.queue(home)] == [waiting.job_id]
    assert [j.job_id for j in reporting.dead_letters(home)] == [dead.job_id]
    assert len(reporting.jobs(home)) == 3
    assert [j.job_id for j in reporting.jobs(home, state=JobState.DONE)] == [
        done.job_id
    ]
    assert [j.job_id for j in reporting.jobs(home, task_id="3")] == [waiting.job_id]
    assert len(reporting.jobs(home, batch_id="b1", state=JobState.PENDING)) == 1


def test_queue_lists_pending_then_running(home):
    with JobStore(home=home) as store:
        first, second = queue.submit(
            store, [queue.JobSpec("a", "1", "x"), queue.JobSpec("a", "2", "x")], "b"
        )
        store.mark_running(first.job_id)
    assert [j.job_id for j in reporting.queue(home)] == [second.job_id, first.job_id]


def test_batches_pending_report(home, seeded):
    assert reporting.batches_pending_report(home) == []  # a job is still PENDING
    with JobStore(home=home) as store:
        waiting = seeded[2]
        store.mark_running(waiting.job_id)
        store.dead_letter(waiting.job_id, DeadReason.KILLED_BY_SIGNAL)
    assert reporting.batches_pending_report(home) == ["b1"]
    with JobStore(home=home) as store:
        store.mark_reported([j.job_id for j in store.list_by_batch("b1")])
    assert reporting.batches_pending_report(home) == []


def test_reporting_never_writes_a_job_row(home, seeded):
    before = [j for j in reporting.jobs(home)]
    reporting.batch_report(home, "b1")
    reporting.queue(home)
    reporting.dead_letters(home)
    assert reporting.jobs(home) == before


def _usage_db(home, rows):
    conn = sqlite3.connect(home / "usage.db")
    conn.execute("CREATE TABLE usage (ts TEXT, kind TEXT, name TEXT, context TEXT)")
    conn.executemany("INSERT INTO usage VALUES (?, ?, ?, '{}')", rows)
    conn.commit()
    conn.close()


def test_usage_summary_groups_by_kind_and_name(home):
    now = datetime.now(timezone.utc)

    def at(days):
        return (now - timedelta(days=days)).isoformat(timespec="milliseconds")

    home.mkdir(parents=True, exist_ok=True)
    _usage_db(
        home,
        [
            (at(1), "job", "odoo-dev:implement"),
            (at(2), "job", "odoo-dev:implement"),
            (at(3), "skill", "odoo-dev:plan"),
            (at(90), "skill", "old"),
        ],
    )
    summary = reporting.usage_summary(home)
    assert summary == [
        {"kind": "job", "name": "odoo-dev:implement", "count": 2, "last_used": at(1)},
        {"kind": "skill", "name": "odoo-dev:plan", "count": 1, "last_used": at(3)},
    ]
    assert len(reporting.usage_summary(home, since_days=365)) == 3


def test_usage_summary_filters_by_kind(home):
    home.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    _usage_db(home, [(stamp, "job", "a"), (stamp, "skill", "b")])
    assert [i["name"] for i in reporting.usage_summary(home, kind="skill")] == ["b"]


def test_state_home_follows_the_environment(tmp_path):
    assert reporting.state_home({"ODOO_DEV_HOME": str(tmp_path)}) == tmp_path


def test_friction_lists_every_task_newest_first(home):
    from odoo_dev_data import tasks

    entry = {"tool": "t", "expected": "e", "observed": "o", "did": "d"}
    tasks.append_friction(7, [entry], job_id="j7", home=home)
    tasks.append_friction(3, [entry], job_id="j3", home=home)
    (home / "tasks" / "notes").mkdir()  # not a task directory
    found = reporting.friction(home)
    assert [e["task_id"] for e in found] == ["3", "7"]
    assert set(found[0]) == {
        "task_id", "job_id", "recorded_at", "tool", "expected", "observed", "did",
    }  # fmt: skip
    assert found[0]["job_id"] == "j3" and found[0]["tool"] == "t"
    assert reporting.friction(home / "absent") == []


def test_usage_summary_without_a_file_is_empty(home):
    assert reporting.usage_summary(home) == []
    home.mkdir(parents=True, exist_ok=True)
    (home / "usage.db").write_bytes(b"")  # a database with no table
    assert reporting.usage_summary(home) == []
