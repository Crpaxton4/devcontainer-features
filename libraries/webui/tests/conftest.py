"""Fixtures. The table shapes are copied from the producers, not imported.

jobs table:  libraries/odoo_client/src/odoo_client/state/jobs.py (JOBS_MIGRATIONS)
runs tables: libraries/odoo_client/src/odoo_client/state/runs_store.py (runs, run_envelopes, run_artifacts)
The generated ``*_at`` columns, the triggers and STRICT are left out: the pages read only the *_us columns.
"""

import json
import sqlite3

import pytest

from webui import create_app

JOBS_SCHEMA = """
CREATE TABLE jobs (
    job_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, task_id TEXT NOT NULL,
    repo TEXT NOT NULL, round INTEGER NOT NULL DEFAULT 1,
    state TEXT NOT NULL CHECK (state IN ('PENDING','RUNNING','DONE','DEAD_LETTER')),
    brief TEXT NOT NULL, submitted_us INTEGER NOT NULL, started_us INTEGER,
    ended_us INTEGER, session_id TEXT NOT NULL DEFAULT '',
    container TEXT NOT NULL DEFAULT '', worktree TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT '', dead_reason TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT ''
)"""
RUNS_SCHEMA = (
    """
CREATE TABLE runs (
    session_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, task_id TEXT NOT NULL,
    repo TEXT NOT NULL DEFAULT '', branch TEXT NOT NULL DEFAULT '',
    pr_num INTEGER NOT NULL DEFAULT 0, started_us INTEGER NOT NULL,
    ended_us INTEGER NOT NULL, outcome TEXT NOT NULL, tool_version TEXT NOT NULL,
    transcript TEXT NOT NULL DEFAULT '', brief TEXT NOT NULL DEFAULT ''
)""",
    """
CREATE TABLE run_envelopes (
    session_id TEXT PRIMARY KEY REFERENCES runs(session_id),
    envelope TEXT NOT NULL CHECK (json_valid(envelope))
)""",
    """
CREATE TABLE run_artifacts (
    job_id TEXT PRIMARY KEY, session_id TEXT NOT NULL DEFAULT '', dir TEXT NOT NULL,
    has_task_json INTEGER NOT NULL DEFAULT 0, has_session INTEGER NOT NULL DEFAULT 0,
    has_envelope INTEGER NOT NULL DEFAULT 0, bytes INTEGER NOT NULL DEFAULT 0,
    harvested_us INTEGER NOT NULL, missing TEXT NOT NULL DEFAULT ''
)""",
)

T0 = 1_760_000_000_000_000
ENVELOPE = {
    "type": "result",
    "usage": {
        "input_tokens": 111,
        "output_tokens": 222,
        "cache_creation_input_tokens": 333,
        "cache_read_input_tokens": 444,
    },
    "total_cost_usd": 1.2345,
    "num_turns": 7,
    "duration_ms": 98765,
}


def _job(job_id, task, state, **extra):
    row = {
        "job_id": job_id,
        "batch_id": "b1",
        "task_id": task,
        "repo": "acme/repo",
        "round": 1,
        "state": state,
        "brief": f"brief for {task}",
        "submitted_us": T0,
        "started_us": None,
        "ended_us": None,
        "session_id": "",
        "container": "",
        "worktree": "",
        "outcome": "",
        "dead_reason": "",
        "detail": "",
    }
    row.update(extra)
    return row


JOBS = (
    _job("j-pending", "T-100", "PENDING"),
    _job(
        "j-running",
        "T-200",
        "RUNNING",
        started_us=T0 + 1,
        session_id="s-running",
        container="ctr-200",
        worktree="/wt/200",
    ),
    _job(
        "j-done",
        "T-300",
        "DONE",
        started_us=T0 + 1,
        ended_us=T0 + 2_000_000,
        session_id="s-done",
        outcome="COMPLETED",
    ),
    _job(
        "j-dead",
        "T-400",
        "DEAD_LETTER",
        started_us=T0 + 1,
        ended_us=T0 + 2_000_000,
        dead_reason="deadline",
    ),
)


def _insert(conn, table, row):
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    """A state dir with jobs.db (one job per state) and runs.db (one run for j-done)."""
    root = tmp_path / "state"
    root.mkdir()
    monkeypatch.setenv("ODOO_DISPATCH_STATE_DIR", str(root))
    monkeypatch.setenv("DISPATCH_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("ODOO_ORCHESTRATOR_HOME", str(tmp_path / "orch"))
    jobs = sqlite3.connect(root / "jobs.db")
    jobs.execute(JOBS_SCHEMA)
    for job in JOBS:
        _insert(jobs, "jobs", job)
    jobs.commit()
    jobs.close()
    runs = sqlite3.connect(root / "runs.db")
    for ddl in RUNS_SCHEMA:
        runs.execute(ddl)
    _insert(
        runs,
        "runs",
        {
            "session_id": "s-done",
            "job_id": "j-done",
            "task_id": "T-300",
            "repo": "acme/repo",
            "branch": "feat/300",
            "pr_num": 42,
            "started_us": T0 + 1,
            "ended_us": T0 + 2_000_000,
            "outcome": "COMPLETED",
            "tool_version": "1.2.3",
        },
    )
    _insert(
        runs,
        "run_envelopes",
        {"session_id": "s-done", "envelope": json.dumps(ENVELOPE)},
    )
    _insert(
        runs,
        "run_artifacts",
        {
            "job_id": "j-done",
            "session_id": "s-done",
            "dir": str(tmp_path / "artifacts" / "j-done"),
            "has_task_json": 1,
            "bytes": 10,
            "harvested_us": T0 + 3,
        },
    )
    runs.commit()
    runs.close()
    return root


@pytest.fixture
def artifacts_dir(tmp_path):
    """artifacts/j-done/ with two files, plus a secret file outside it."""
    job = tmp_path / "artifacts" / "j-done"
    job.mkdir(parents=True)
    (job / "task.json").write_text('{"task": "T-300"}')
    (job / "envelope.json").write_text("{}")
    (tmp_path / "artifacts" / "secret.txt").write_text("secret")
    return job


@pytest.fixture
def orch_home(tmp_path):
    """Orchestrator home: T-100 has a job; T-500 is a draft; T-600 is approved."""
    home = tmp_path / "orch"
    (home / "briefs" / "T-100").mkdir(parents=True)
    (home / "briefs" / "T-100" / "draft-1.md").write_text("x")
    (home / "briefs" / "T-500").mkdir(parents=True)
    (home / "briefs" / "T-500" / "draft-1.md").write_text("x")
    (home / "approved").mkdir()
    (home / "approved" / "T-600.txt").write_text("/some/brief.md\n")
    (home / "approved" / "T-100.txt").write_text("/other.md\n")
    (home / "friction").mkdir()
    (home / "friction" / "2026-01-02.md").write_text("orchestrator friction")
    return home


@pytest.fixture
def client(state_dir):
    return create_app().test_client()


@pytest.fixture
def empty_client(tmp_path, monkeypatch):
    """An app on a host with no state: no jobs.db, no runs.db, no orchestrator home."""
    monkeypatch.setenv("ODOO_DISPATCH_STATE_DIR", str(tmp_path / "none"))
    monkeypatch.setenv("DISPATCH_ARTIFACTS_DIR", str(tmp_path / "none-art"))
    monkeypatch.setenv("ODOO_ORCHESTRATOR_HOME", str(tmp_path / "none-orch"))
    return create_app().test_client()
