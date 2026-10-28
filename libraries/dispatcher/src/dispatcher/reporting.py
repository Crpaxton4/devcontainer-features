"""The read-only reporting API of the dispatcher.

``dispatch report|dead-letters``, the orchestrator and the web UI read the job
rows through these functions. Nothing here writes a job row: each call opens
``state.db`` through :class:`odoo_dev_data.jobs.JobStore`, reads, and closes.
``usage.db`` is opened read-only (``mode=ro``).
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from odoo_dev_data.home import paths, resolve_home
from odoo_dev_data.jobs import Job, JobState, JobStore
from odoo_dev_data.tasks import read_task


def state_home(env: Mapping[str, str] | None = None) -> Path:
    """The state root: ``$ODOO_DEV_HOME`` or the default."""
    return resolve_home(env)


def jobs(
    home: Path,
    *,
    state: JobState | None = None,
    batch_id: str | None = None,
    task_id: str | None = None,
) -> list[Job]:
    """Jobs filtered by state, batch and task id, oldest submission first."""
    with JobStore(home=home) as store:
        if batch_id is not None:
            found = store.list_by_batch(batch_id)
        else:
            states = [state] if state is not None else list(JobState)
            found = [j for s in states for j in store.list_by_state(s)]
    found = [j for j in found if _matches(j, state, task_id)]
    if batch_id is not None:
        return found  # the store's insertion order
    return sorted(found, key=lambda j: (j.submitted_us, j.job_id))


def _matches(job: Job, state: JobState | None, task_id: str | None) -> bool:
    if state is not None and job.state != state:
        return False
    return task_id is None or job.task_id == str(task_id)


def queue(home: Path) -> list[Job]:
    """PENDING jobs, then RUNNING jobs, each oldest first."""
    with JobStore(home=home) as store:
        return store.list_by_state(JobState.PENDING) + store.list_by_state(
            JobState.RUNNING
        )


def dead_letters(home: Path) -> list[Job]:
    """The dead letter list."""
    with JobStore(home=home) as store:
        return store.dead_letters()


def batches_pending_report(home: Path) -> list[str]:
    """Batches that are all finished and have a job not yet reported."""
    with JobStore(home=home) as store:
        return store.list_batches_pending_report()


def _seconds(job: Job) -> float | None:
    window = job.billable_window
    return None if window is None else (window[1] - window[0]) / 1_000_000


def _iso(us: int | None) -> str | None:
    if us is None:
        return None
    return datetime.fromtimestamp(us / 1_000_000, timezone.utc).isoformat()


def _structured(job: Job) -> dict | None:
    try:
        envelope = job.envelope_json()
    except ValueError:
        return None
    value = envelope.get("structured_output") if isinstance(envelope, dict) else None
    return value if isinstance(value, dict) else None


def job_entry(job: Job) -> dict:
    """One job of a batch report."""
    return {
        "job_id": job.job_id,
        "task": job.task_id,
        "state": job.state.value,
        "outcome": job.outcome,
        "dead_reason": job.dead_reason,
        "detail": job.detail,
        "started_at": _iso(job.started_us),
        "ended_at": _iso(job.ended_us),
        "duration_s": _seconds(job),
        "branch": job.branch,
        "pr_num": job.pr_num,
        "bundle": job.bundle_dir or None,
        "question_count": job.question_count,
        "input_tokens": job.input_tokens,
        "output_tokens": job.output_tokens,
        "cost_usd": job.cost_usd,
        "structured": _structured(job),
        "reported": job.reported_us is not None,
    }


def batch_report(home: Path, batch_id: str) -> dict:
    """One batch as the orchestrator reads it: its jobs and their totals."""
    entries = [job_entry(job) for job in jobs(home, batch_id=batch_id)]
    return {
        "batch_id": batch_id,
        "jobs": entries,
        "totals": {
            "jobs": len(entries),
            "done": sum(e["state"] == JobState.DONE.value for e in entries),
            "dead": sum(e["state"] == JobState.DEAD_LETTER.value for e in entries),
            "cost_usd": sum(e["cost_usd"] for e in entries),
            "questions": sum(e["question_count"] for e in entries),
        },
    }


def friction(home: Path) -> list[dict]:
    """Every friction entry of every ``tasks/*/task.json``, newest first.

    Each item has ``task_id``, ``job_id``, ``recorded_at``, ``tool``,
    ``expected``, ``observed`` and ``did``. A task with no readable
    ``task.json`` adds nothing.
    """
    found = []
    for path in sorted(paths(home).tasks.glob("*/task.json")):
        try:
            record = read_task(path.parent.name, home)
        except ValueError:  # a non-numeric directory or a malformed file
            continue
        for entry in record.friction:
            found.append(
                {
                    "task_id": record.task_id,
                    "job_id": entry.get("job_id", ""),
                    "recorded_at": entry.get("recorded_at", ""),
                    "tool": entry.get("tool", ""),
                    "expected": entry.get("expected", ""),
                    "observed": entry.get("observed", ""),
                    "did": entry.get("did", ""),
                }
            )
    return sorted(found, key=lambda e: e["recorded_at"], reverse=True)


def usage_summary(
    home: Path, *, since_days: int = 30, kind: str | None = None
) -> list[dict]:
    """Usage lines of the last ``since_days`` days grouped by ``kind, name``.

    Each item has ``kind``, ``name``, ``count`` and ``last_used``. ``kind``
    keeps one kind only. The list is empty when ``usage.db`` does not exist.
    """
    path = paths(home).usage_db
    if not path.is_file():
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat(
        timespec="milliseconds"
    )
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT kind, name, COUNT(*), MAX(ts) FROM usage "
            "WHERE ts >= ? AND (? IS NULL OR kind = ?) "
            "GROUP BY kind, name ORDER BY COUNT(*) DESC, kind, name",
            (cutoff, kind, kind),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    return [
        {"kind": kind, "name": name, "count": count, "last_used": last}
        for kind, name, count, last in rows
    ]
