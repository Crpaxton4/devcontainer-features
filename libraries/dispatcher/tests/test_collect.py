"""Recording a result on the job row and collecting the bundle."""

import json
from pathlib import Path

import pytest
from odoo_dev_data.home import job_bundle_dir
from odoo_dev_data.jobs import DeadReason, JobState, JobStore, RunOutcome

from dispatcher import collect, queue
from .support import GOOD, stream, write_task_record


def _parse(lines):
    parser = collect.StreamParser()
    for line in lines:
        parser.feed(line)
    return parser


@pytest.fixture
def store(home):
    with JobStore(home=home) as opened:
        yield opened


@pytest.fixture
def job(store):
    (queued,) = queue.submit(store, [queue.JobSpec("qoc", "42", "brief")], "b")
    return store.mark_running(queued.job_id)


def _collect(store, job, home, parser, *, failed, transcript=None, record=None):
    return collect.record_then_collect(
        store,
        parser=parser,
        job=job,
        started_us=job.started_us,
        ended_us=job.started_us + 5,
        failed=failed,
        transcript=transcript,
        record=record or {},
        home=home,
    )


def test_task_record_helpers(home):
    assert collect.read_task_record("42", home) == {}
    write_task_record(home, "42", "{bad")
    assert collect.read_task_record("42", home) == {}
    write_task_record(home, "42", "[1]")
    assert collect.read_task_record("42", home) == {}
    record = {"build": {"branch": "b", "worktree": "/w"}, "pr": {"number": 7}}
    write_task_record(home, "42", json.dumps(record))
    got = collect.read_task_record("42", home)
    assert (
        collect.branch_of(got),
        collect.worktree_of(got),
        collect.pr_number_of(got),
    ) == ("b", "/w", 7)
    assert collect.read_task_record("43", home) == {}  # another task's record
    assert collect.read_task_record("not-a-number", home) == {}
    assert collect.pr_number_of({"pr": 3}) == 3
    assert collect.pr_number_of({"pr": {"number": True}}) == 0
    assert collect.pr_number_of({}) == 0
    assert collect.branch_of({"build": []}) == "" and collect.worktree_of({}) == ""


@pytest.mark.parametrize(
    "reported,questions,expected",
    [
        ("done", [], RunOutcome.COMPLETED),
        (
            "done",
            [{"question": "q", "assumption": "a", "why": "w"}],
            RunOutcome.COMPLETED_WITH_QUESTIONS,
        ),
        ("partial", [], RunOutcome.COMPLETED_WITH_QUESTIONS),
        ("blocked", [], RunOutcome.BLOCKED),
    ],
)
def test_outcome_uses_the_shared_mapping(reported, questions, expected):
    env = {"structured_output": {**GOOD, "outcome": reported, "questions": questions}}
    assert collect.stored_outcome(env, failed=False) is expected
    assert collect.stored_outcome(env, failed=True) is RunOutcome.FAILED
    assert collect.stored_outcome(None, failed=False) is RunOutcome.FAILED


def test_envelope_usage():
    envelope = {
        "usage": {"input_tokens": 11, "output_tokens": 22},
        "total_cost_usd": 1.5,
    }
    assert collect.envelope_usage(envelope) == (11, 22, 1.5)
    assert collect.envelope_usage({"usage": "x", "total_cost_usd": True}) == (0, 0, 0.0)
    assert collect.envelope_usage(None) == (0, 0, 0.0)


def test_a_success_fills_the_row_and_writes_the_bundle(store, job, home, tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("line\n")
    write_task_record(home, "42", json.dumps({"build": {"branch": "feat"}}))
    parser = _parse(
        stream(
            structured=GOOD,
            usage={"input_tokens": 3, "output_tokens": 4},
            total_cost_usd=0.25,
        )
    )
    write = _collect(
        store,
        job,
        home,
        parser,
        failed=False,
        transcript=transcript,
        record={"build": {"branch": "feat"}, "pr": {"number": 9}},
    )
    assert write.recorded and write.outcome is RunOutcome.COMPLETED
    row = store.get(job.job_id)
    assert row.state is JobState.DONE and row.outcome == "COMPLETED"
    assert (row.session_id, row.branch, row.pr_num) == ("sess-1", "feat", 9)
    assert (row.input_tokens, row.output_tokens, row.cost_usd) == (3, 4, 0.25)
    assert row.tool_version == "9.9.9" and row.question_count == 0
    assert row.transcript_path == str(transcript)
    assert row.envelope_json()["structured_output"]["outcome"] == "done"
    assert row.started_us == job.started_us  # the row's window is the billable one
    bundle = job_bundle_dir("42", job.job_id, home)
    assert row.bundle_dir == str(bundle)
    assert sorted(p.name for p in bundle.iterdir()) == [
        "envelope.json",
        "session.jsonl",
    ]
    assert (bundle / "session.jsonl").read_text() == "line\n"
    assert json.loads((bundle / "envelope.json").read_text())["type"] == "result"
    assert not (bundle / "harvest.json").exists()
    assert not (bundle / "task.json").exists()  # the agent's own copy is the one
    assert (row.has_task_json, row.has_session, row.has_envelope) == (True, True, True)
    assert row.bytes == 5 + len((bundle / "envelope.json").read_text())
    assert row.missing == ""


def test_a_missing_transcript_and_task_json_are_recorded_not_raised(store, job, home):
    parser = _parse(stream(structured=GOOD))
    write = _collect(store, job, home, parser, failed=False, transcript=None)
    row = store.get(job.job_id)
    assert write.recorded and row.state is JobState.DONE
    assert (row.has_task_json, row.has_session, row.has_envelope) == (
        False,
        False,
        True,
    )
    assert "session.jsonl: no transcript path was given" in row.missing
    assert "task.json: the agent wrote no task.json" in row.missing


def test_a_dead_letter_indexes_a_bundle_without_record_result(
    store, job, home, monkeypatch
):
    def boom(*a, **k):
        raise AssertionError("record_result must not run for a dead letter")

    monkeypatch.setattr(store, "record_result", boom)
    parser = _parse(stream())  # an envelope with no structured value
    write = _collect(store, job, home, parser, failed=True)
    assert not write.recorded and write.outcome is RunOutcome.FAILED
    assert store.get(job.job_id).state is JobState.RUNNING
    assert (job_bundle_dir("42", job.job_id, home) / "envelope.json").is_file()
    store.dead_letter(job.job_id, DeadReason.STRUCTURED_OUTPUT_MISSING, detail="d")
    collect.index_collected(store, job.job_id, write)
    row = store.get(job.job_id)
    assert row.state is JobState.DEAD_LETTER and row.outcome == ""
    assert row.bundle_dir == str(job_bundle_dir("42", job.job_id, home))
    assert row.has_envelope and not row.has_session


def test_no_session_still_gets_a_bundle(store, job, home):
    write = _collect(store, job, home, collect.StreamParser(), failed=True)
    store.dead_letter(job.job_id, DeadReason.SESSION_LAUNCH_FAILED)
    collect.index_collected(store, job.job_id, write)
    row = store.get(job.job_id)
    assert Path(row.bundle_dir).is_dir() and not row.has_envelope
    assert "envelope.json: no envelope was received" in row.missing


def test_a_failing_result_write_does_not_stop_collection_and_is_surfaced(
    store, job, home, monkeypatch
):
    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "record_result", boom)
    write = _collect(store, job, home, _parse(stream(structured=GOOD)), failed=False)
    assert isinstance(write.record_error, RuntimeError)
    assert write.bundle is not None and not write.recorded


def test_collection_failure_is_a_note_not_an_error(store, job, home, monkeypatch):
    def boom(*a, **k):
        raise OSError("nope")

    monkeypatch.setattr(collect, "ensure_job_bundle_dir", boom)
    write = _collect(store, job, home, _parse(stream(structured=GOOD)), failed=False)
    assert write.recorded and write.bundle is None
    assert "collection failed" in write.notes[0]
    assert store.get(job.job_id).bundle_dir == ""


def test_the_questions_of_the_envelope_are_counted(store, job, home):
    asked = {**GOOD, "questions": [{"question": "q", "assumption": "a", "why": "w"}]}
    _collect(store, job, home, _parse(stream(structured=asked)), failed=False)
    row = store.get(job.job_id)
    assert row.question_count == 1 and row.outcome == "COMPLETED_WITH_QUESTIONS"
