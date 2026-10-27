"""The friction entries recorded in the task records, grouped by task."""

from itertools import groupby

from dispatcher import reporting
from flask import Blueprint, current_app, render_template

bp = Blueprint("friction", __name__)


@bp.get("/friction")
def index():
    entries = reporting.friction(current_app.config["ODOO_DEV_HOME"])
    # Newest first overall: a task sits where its newest entry is.
    by_task = sorted(entries, key=lambda e: e["task_id"])
    groups = [
        (task, list(rows)) for task, rows in groupby(by_task, lambda e: e["task_id"])
    ]
    groups.sort(key=lambda g: g[1][0]["recorded_at"], reverse=True)
    return render_template("friction.html", groups=groups)
