"""Usage page: the usage lines of the last N days, grouped by kind and name."""

from dispatcher import reporting
from flask import Blueprint, Response, abort, current_app, render_template, request

bp = Blueprint("usage", __name__)


@bp.get("/usage")
def index():
    try:
        days = int(request.args.get("days", "30"))
        if days < 0:
            raise ValueError
    except ValueError:
        abort(
            Response("Days must be a whole number", status=400, mimetype="text/plain")
        )
    kind = request.args.get("kind") or None
    home = current_app.config["ODOO_DEV_HOME"]
    items = reporting.usage_summary(home, since_days=days, kind=kind)
    template = (
        "_usage_table.html" if request.headers.get("HX-Request") else "usage.html"
    )
    response = Response(
        render_template(template, items=items, days=days, kind=kind or ""),
        mimetype="text/html",
    )
    response.headers["Vary"] = "HX-Request"
    return response
