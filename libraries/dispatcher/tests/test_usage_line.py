"""The one ``job`` usage line a finished job writes (#1204)."""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from dispatcher import queue
from dispatcher.collect import StreamParser
from dispatcher.engine import job_usage_context
from odoo_dev_data.jobs import JobState
from .support import GOOD, FakeProc, stream
from .test_engine_cli import _submit, world  # noqa: F401  (the engine fixture)

USAGE = {
    "input_tokens": 11,
    "output_tokens": 22,
    "cache_creation_input_tokens": 33,
    "cache_read_input_tokens": 44,
}


@pytest.fixture
def log_dir(home):
    """The state root: the usage line lands in ``<home>/usage.db``."""
    home.mkdir(parents=True, exist_ok=True)
    return home


def _lines(directory):
    conn = sqlite3.connect(directory / "usage.db")
    try:
        return conn.execute("SELECT kind, name, context FROM usage").fetchall()
    finally:
        conn.close()


def _parser(**result):
    parser = StreamParser()
    for line in stream(**result):
        parser.feed(line)
    return parser


def test_context_copies_the_envelope_figures_and_calculates_nothing():
    parser = _parser(usage=USAGE, total_cost_usd=0.5, duration_ms=1234)
    job = SimpleNamespace(job_id="j1", session_id="")
    context = job_usage_context(job, parser, True)
    assert context == {
        "job_id": "j1",
        "session_id": "sess-1",
        "source": "dispatcher",
        "ok": True,
        **USAGE,
        "total_cost_usd": 0.5,
        "num_turns": 3,
        "duration_ms": 1234,
    }


def test_context_without_an_envelope_keeps_the_ids():
    job = SimpleNamespace(job_id="j1", session_id="s9")
    assert job_usage_context(job, StreamParser(), False) == {
        "job_id": "j1",
        "session_id": "s9",
        "source": "dispatcher",
        "ok": False,
    }


def test_a_finished_job_writes_one_job_line(world, log_dir):  # noqa: F811
    engine, shell, _ = world
    shell.proc = FakeProc(
        stream(structured=GOOD, usage=USAGE, total_cost_usd=1.25, duration_ms=99)
    )
    (job,) = _submit(engine, ("qoc", "10"))
    engine.run()
    assert engine.jobs.get(job.job_id).state is JobState.DONE
    ((kind, name, context),) = _lines(log_dir)
    assert (kind, name) == ("job", engine.settings.entry.lstrip("/"))
    figures = json.loads(context)
    assert figures["job_id"] == job.job_id and figures["session_id"] == "sess-1"
    assert {k: figures[k] for k in USAGE} == USAGE
    assert figures["total_cost_usd"] == 1.25 and figures["num_turns"] == 3
    assert figures["duration_ms"] == 99 and figures["ok"] is True


def test_the_usage_line_names_the_job_entry(world, log_dir):  # noqa: F811
    engine, shell, _ = world
    shell.proc = FakeProc(stream(structured=GOOD, usage=USAGE))
    queue.submit(
        engine.jobs,
        [queue.JobSpec("qoc", "11", "b", entry="/odoo-dev:upgrade-implement")],
        "b",
    )
    engine.run()
    ((kind, name, _context),) = _lines(log_dir)
    assert (kind, name) == ("job", "odoo-dev:upgrade-implement")


def test_a_failing_usage_log_does_not_fail_the_job(
    world, log_dir, monkeypatch
):  # noqa: F811
    engine, shell, _ = world

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr("dispatcher.engine.usage_log.record", boom)
    shell.proc = FakeProc(stream(structured=GOOD))
    (job,) = _submit(engine, ("qoc", "10"))
    engine.run()
    assert engine.jobs.get(job.job_id).state is JobState.DONE
    assert not engine.errors


def test_the_off_switch_writes_nothing(world, home, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ODOO_DEV_USAGE_LOG", "0")
    engine, shell, _ = world
    shell.proc = FakeProc(stream(structured=GOOD))
    _submit(engine, ("qoc", "10"))
    engine.run()
    assert not (home / "usage.db").exists()
