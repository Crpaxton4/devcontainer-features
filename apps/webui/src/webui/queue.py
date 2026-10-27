"""The queue: PENDING and RUNNING jobs, and the batches waiting for their report."""

from dispatcher import reporting
from flask import Blueprint, current_app, render_template

bp = Blueprint("queue", __name__)


@bp.get("/queue")
def index():
    home = current_app.config["ODOO_DEV_HOME"]
    return render_template(
        "queue.html",
        entries=[reporting.job_entry(j) for j in reporting.queue(home)],
        batches=reporting.batches_pending_report(home),
    )
