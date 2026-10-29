"""Jobs list, job detail and the bundle files of one job."""

import json
from pathlib import Path

from dispatcher import reporting
from flask import Blueprint, Response, abort, current_app, render_template, request

bp = Blueprint("jobs", __name__)

#: The bundle files the detail page links to, when they exist.
BUNDLE_FILES = ("envelope.json", "session.jsonl", "STOP")


#: Transcript panel limits: entries shown, and characters kept per tool input/result.
TRANSCRIPT_TAIL = 80
TOOL_INPUT_MAX = 200
TOOL_RESULT_MAX = 400


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


def _block_text(block) -> str:
    """One readable line for a transcript content block, or "" to skip it."""
    if isinstance(block, str):
        return block
    kind = block.get("type")
    if kind == "text":
        return block.get("text", "")
    if kind == "tool_use":
        summary = json.dumps(block.get("input", {}), ensure_ascii=False)
        return f"[{block.get('name', '?')}] {summary[:TOOL_INPUT_MAX]}"
    if kind == "tool_result":
        content = block.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") for b in content if isinstance(b, dict)
            )
        return f"-> {str(content)[:TOOL_RESULT_MAX]}"
    return ""


def _transcript_lines(path: Path) -> list[str]:
    """The last TRANSCRIPT_TAIL renderable entries of a transcript file."""
    out = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            message = json.loads(raw).get("message")
        except (ValueError, AttributeError):
            continue
        content = message.get("content") if isinstance(message, dict) else None
        blocks = [content] if isinstance(content, str) else content or []
        text = "\n".join(t for t in map(_block_text, blocks) if t.strip())
        if text:
            out.append(text)
    return out[-TRANSCRIPT_TAIL:]


@bp.get("/jobs/<job_id>/transcript")
def transcript(job_id: str):
    """The tail of the live session transcript as an HTML fragment."""
    job = _job(job_id)
    path = reporting.transcript_path(current_app.config["CLAUDE_HOME"], job.session_id)
    lines = _transcript_lines(path) if path else None
    return render_template("_transcript.html", lines=lines)


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
