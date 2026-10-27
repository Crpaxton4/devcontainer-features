"""Jobs list, job detail and the artifact files of one job."""

import json
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, Response, abort, render_template, request

from webui import paths
from webui.db import rows

bp = Blueprint("jobs", __name__)

STATES = ("PENDING", "RUNNING", "DONE", "DEAD_LETTER")
USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)
_JOB_COLUMNS = (
    "job_id, batch_id, task_id, repo, round, state, submitted_us, started_us, "
    "ended_us, session_id, container, worktree, outcome, dead_reason, detail"
)


def format_us(value):
    """Microseconds since the epoch as an UTC time string; '' for NULL."""
    if not value:
        return ""
    return datetime.fromtimestamp(value / 1_000_000, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def result_figures(envelope_text: str) -> dict:
    """Token and cost figures of a stored result envelope; {} when unusable."""
    try:
        envelope = json.loads(envelope_text)
    except (TypeError, ValueError):
        return {}
    if not isinstance(envelope, dict):
        return {}
    usage = envelope.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    figures = {key: usage.get(key) for key in USAGE_KEYS}
    for key in ("total_cost_usd", "num_turns", "duration_ms"):
        figures[key] = envelope.get(key)
    return figures


def _job_dir(job_id: str) -> Path:
    if not job_id or job_id in (".", "..") or "/" in job_id or "\\" in job_id:
        abort(404)
    return paths.artifacts_root() / job_id


def _artifact_files(job_dir: Path) -> list[str]:
    if not job_dir.is_dir():
        return []
    return sorted(
        str(p.relative_to(job_dir)) for p in job_dir.rglob("*") if p.is_file()
    )


@bp.get("/jobs")
def index():
    jobs = rows(
        paths.jobs_db(),
        f"SELECT {_JOB_COLUMNS} FROM jobs ORDER BY submitted_us DESC",
    )
    grouped = {state: [j for j in jobs if j["state"] == state] for state in STATES}
    template = "_jobs_table.html" if request.headers.get("HX-Request") else "jobs.html"
    response = Response(
        render_template(template, grouped=grouped, format_us=format_us),
        mimetype="text/html",
    )
    response.headers["Vary"] = "HX-Request"
    return response


@bp.get("/jobs/<job_id>")
def detail(job_id: str):
    job_dir = _job_dir(job_id)
    found = rows(
        paths.jobs_db(),
        f"SELECT {_JOB_COLUMNS}, brief FROM jobs WHERE job_id = ?",
        (job_id,),
    )
    if not found:
        abort(404)
    job = found[0]
    session = job["session_id"] or job_id
    runs = rows(
        paths.runs_db(),
        "SELECT session_id, job_id, task_id, repo, branch, pr_num, started_us, "
        "ended_us, outcome, tool_version FROM runs WHERE job_id = ? OR session_id = ?",
        (job_id, session),
    )
    envelopes = []
    for run in runs:
        for row in rows(
            paths.runs_db(),
            "SELECT envelope FROM run_envelopes WHERE session_id = ?",
            (run["session_id"],),
        ):
            envelopes.append(
                {
                    "session_id": run["session_id"],
                    "text": row["envelope"],
                    "figures": result_figures(row["envelope"]),
                }
            )
    stored = rows(
        paths.runs_db(),
        "SELECT job_id, session_id, dir, has_task_json, has_session, has_envelope, "
        "bytes, harvested_us, missing FROM run_artifacts WHERE job_id = ?",
        (job_id,),
    )
    return render_template(
        "job.html",
        job=job,
        runs=runs,
        envelopes=envelopes,
        stored=stored,
        files=_artifact_files(job_dir),
        usage_keys=USAGE_KEYS,
        format_us=format_us,
    )


@bp.get("/jobs/<job_id>/files/<path:name>")
def artifact(job_id: str, name: str):
    """Show one file under ``artifacts/<job_id>/`` as plain text; 404 for any other path."""
    job_dir = _job_dir(job_id).resolve()
    target = (job_dir / name).resolve()
    if not target.is_relative_to(job_dir) or not target.is_file():
        abort(404)
    text = target.read_text(encoding="utf-8", errors="replace")
    return Response(text, mimetype="text/plain; charset=utf-8")
