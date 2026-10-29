"""Jobs list, job detail and the bundle files of one job."""

import json
from pathlib import Path

from dispatcher import answers as dispatcher_answers
from dispatcher import reporting
from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    url_for,
)

bp = Blueprint("jobs", __name__)

#: The bundle files the detail page links to, when they exist.
BUNDLE_FILES = ("envelope.json", "session.jsonl", "STOP")


#: Raw lines of the live transcript shown while the job runs.
TRANSCRIPT_TAIL = 80

#: Job states in which the stream panel keeps polling.
POLLING_STATES = ("PENDING", "RUNNING")


def _job(job_id: str):
    for job in reporting.jobs(current_app.config["ODOO_DEV_HOME"]):
        if job.job_id == job_id:
            return job
    abort(404)


def _bundle(job) -> Path | None:
    return Path(job.bundle_dir) if job.bundle_dir else None


@bp.get("/jobs")
def index():
    home = current_app.config["ODOO_DEV_HOME"]
    entries = [reporting.job_entry(j) for j in reversed(reporting.jobs(home))]
    template = "_jobs_table.html" if request.headers.get("HX-Request") else "jobs.html"
    response = Response(
        render_template(template, entries=entries), mimetype="text/html"
    )
    response.headers["Vary"] = "HX-Request"
    return response


@bp.get("/jobs/<job_id>")
def detail(job_id: str):
    job = _job(job_id)
    entry = reporting.job_entry(job)
    structured = entry.pop("structured")
    bundle = _bundle(job)
    files = [n for n in BUNDLE_FILES if bundle and (bundle / n).is_file()]
    return render_template(
        "job.html",
        entry=entry,
        structured=json.dumps(structured, indent=2, sort_keys=True),
        files=files,
        questions=_questions(structured) if job.state.value == "DONE" else [],
        answers=json.loads(job.answers) if job.answers else None,
    )


def _questions(structured: dict | None) -> list[dict]:
    """The ``{question, assumption, why}`` objects of the structured output."""
    found = structured.get("questions") if structured else None
    return [q for q in found if isinstance(q, dict)] if isinstance(found, list) else []


@bp.post("/jobs/<job_id>/answers")
def save_answers(job_id: str):
    """Save one answer per question; a blank field accepts the assumption."""
    job = _job(job_id)
    questions = _questions(reporting.job_entry(job)["structured"])
    if job.state.value != "DONE" or not questions:
        abort(409)
    accept_all = "accept_all" in request.form
    answers = []
    for index, item in enumerate(questions):
        text = "" if accept_all else request.form.get(f"answer_{index}", "").strip()
        answers.append(
            {"question": item.get("question", ""), "accepted": not text, "answer": text}
        )
    dispatcher_answers.save_answers(
        current_app.config["ODOO_DEV_HOME"], job_id, answers
    )
    return redirect(url_for("jobs.detail", job_id=job_id))


def _stream_lines(job) -> list[str] | None:
    """Raw session lines: the live tail while the job runs, else the bundle file."""
    if job.state.value == "RUNNING":
        path = reporting.transcript_path(
            current_app.config["CLAUDE_HOME"], job.session_id
        )
        tail = TRANSCRIPT_TAIL
    else:
        bundle = _bundle(job)
        path = bundle / "session.jsonl" if bundle else None
        tail = None
    if path is None or not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-tail:] if tail else lines


@bp.get("/jobs/<job_id>/transcript")
def transcript(job_id: str):
    """The session stream as an HTML fragment that polls until the job ends."""
    job = _job(job_id)
    return render_template(
        "_transcript.html",
        lines=_stream_lines(job),
        polling=job.state.value in POLLING_STATES,
        job_id=job.job_id,
    )


@bp.get("/jobs/<job_id>/files/<name>")
def bundle_file(job_id: str, name: str):
    """Show one file of the job bundle as plain text; 404 for any other name."""
    bundle = _bundle(_job(job_id))
    if bundle is None or name in ("", ".", "..") or "/" in name or "\\" in name:
        abort(404)
    root = bundle.resolve()
    target = (root / name).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        abort(404)
    text = target.read_text(encoding="utf-8", errors="replace")
    return Response(text, mimetype="text/plain; charset=utf-8")
