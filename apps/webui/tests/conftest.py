"""Fixtures: one temp ``ODOO_DEV_HOME`` seeded through the real stores."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from odoo_dev_data.jobs import DeadReason, JobStore
from odoo_dev_data.tasks import append_friction

from webui import create_app

ENTRY = {
    "tool": "odoo-dev",
    "expected": "a diff",
    "observed": "no diff",
    "did": "retry",
}


def _write_usage(home, rows):
    conn = sqlite3.connect(home / "usage.db")
    conn.execute("CREATE TABLE usage (ts TEXT, kind TEXT, name TEXT, context TEXT)")
    conn.executemany("INSERT INTO usage VALUES (?, ?, ?, '{}')", rows)
    conn.commit()
    conn.close()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A state root with a PENDING, a RUNNING, a DONE and a dead job, plus friction."""
    root = tmp_path / "odoo-dev"
    root.mkdir()
    monkeypatch.setenv("ODOO_DEV_HOME", str(root))
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "envelope.json").write_text('{"type": "result"}')
    (bundle / "session.jsonl").write_text('{"line": 1}\n')
    (tmp_path / "secret.txt").write_text("secret")
    with JobStore(home=root) as store:
        for job_id, task in (("j-pending", "100"), ("j-running", "200")):
            store.submit(
                job_id=job_id, batch_id="b1", task_id=task, repo="r", brief="x"
            )
        store.mark_running("j-running")
        store.set_session("j-running", "sess-1")
        store.submit(job_id="j-done", batch_id="b1", task_id="300", repo="r", brief="x")
        store.mark_running("j-done")
        store.record_result(
            "j-done",
            outcome="COMPLETED",
            session_id="s",
            envelope={
                "structured_output": {
                    "outcome": "done",
                    "summary": "ok",
                    "questions": [
                        {
                            "question": "Which tax?",
                            "assumption": "VAT",
                            "why": "unclear",
                        },
                        {
                            "question": "Which team?",
                            "assumption": "Sales",
                            "why": "none set",
                        },
                    ],
                }
            },
            cost_usd=1.25,
            question_count=3,
        )
        store.index_bundle("j-done", bundle_dir=str(bundle), has_envelope=True)
        store.submit(job_id="j-dead", batch_id="b1", task_id="400", repo="r", brief="x")
        store.mark_running("j-dead")
        store.dead_letter("j-dead", DeadReason.SESSION_EXIT_NONZERO, detail="boom")
    append_friction(300, [ENTRY], job_id="j-done", home=root)
    return root


@pytest.fixture
def usage_db(home):
    stamp = datetime.now(timezone.utc) - timedelta(days=1)
    _write_usage(
        home,
        [
            (stamp.isoformat(timespec="milliseconds"), "skill", "odoo-dev:plan"),
            (stamp.isoformat(timespec="milliseconds"), "job", "implement"),
        ],
    )


@pytest.fixture
def claude_home(tmp_path, monkeypatch):
    """A Claude home holding the project directory of the transcripts."""
    root = tmp_path / "claude"
    (root / "projects" / "slug").mkdir(parents=True)
    monkeypatch.setenv("DISPATCH_CLAUDE_HOME", str(root))
    return root


@pytest.fixture
def client(home, claude_home):
    return create_app().test_client()


@pytest.fixture
def empty_client(tmp_path, monkeypatch):
    """An app on a host with no state at all."""
    monkeypatch.setenv("ODOO_DEV_HOME", str(tmp_path / "none"))
    return create_app().test_client()
