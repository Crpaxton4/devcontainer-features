"""The orchestrator queue: briefs and approved tasks the dispatcher has not taken.

A task is "not taken yet" when it has a draft under ``briefs/<task>/`` or an
approved file ``approved/<task>.txt``, and ``jobs.db`` has no row of any state
with that ``task_id``. A task whose only job is DEAD_LETTER counts as taken:
the dispatcher has taken it, and a re-queue adds a new row for the same task.
Without ``jobs.db`` no task is taken.
"""

from flask import Blueprint, render_template

from webui import paths
from webui.db import rows

bp = Blueprint("queue", __name__)


def not_taken() -> list[dict]:
    home = paths.orchestrator_home()
    tasks: dict[str, dict] = {}
    briefs = home / "briefs"
    if briefs.is_dir():
        for folder in sorted(p for p in briefs.iterdir() if p.is_dir()):
            drafts = sorted(p.name for p in folder.glob("draft-*.md"))
            tasks.setdefault(folder.name, {"drafts": [], "approved": ""})[
                "drafts"
            ] = drafts
    approved = home / "approved"
    if approved.is_dir():
        for path in sorted(approved.glob("*.txt")):
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            tasks.setdefault(path.stem, {"drafts": [], "approved": ""})[
                "approved"
            ] = text
    taken = {r["task_id"] for r in rows(paths.jobs_db(), "SELECT task_id FROM jobs")}
    return [{"task": t, **v} for t, v in sorted(tasks.items()) if t not in taken]


@bp.get("/queue")
def index():
    return render_template("queue.html", tasks=not_taken())
