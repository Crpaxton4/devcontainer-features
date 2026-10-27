"""Jobs list, job detail and the bundle files of one job."""

import json
from pathlib import Path

from dispatcher import reporting
from flask import Blueprint, Response, abort, current_app, render_template, request

bp = Blueprint("jobs", __name__)

#: The bundle files the detail page links to, when they exist.
BUNDLE_FILES = ("envelope.json", "session.jsonl", "STOP")


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
