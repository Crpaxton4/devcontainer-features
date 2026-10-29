"""A read-only local web interface. One Flask app, one blueprint per page group.

To add a page group: write a module with a ``bp = Blueprint(...)`` and add one
line to ``PAGES`` below. ``PAGES`` is the only registration point. It also
builds the navigation of ``base.html``.
"""

from dispatcher import reporting
from flask import Flask, redirect, url_for

from webui import friction, jobs, queue, usage

#: (blueprint, nav label, endpoint the nav link points to)
PAGES = (
    (jobs.bp, "Jobs", "jobs.index"),
    (queue.bp, "Queue", "queue.index"),
    (friction.bp, "Friction", "friction.index"),
    (usage.bp, "Usage", "usage.index"),
)


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["ODOO_DEV_HOME"] = reporting.state_home()
    app.config["CLAUDE_HOME"] = reporting.claude_home()
    for blueprint, _label, _endpoint in PAGES:
        app.register_blueprint(blueprint)

    @app.context_processor
    def nav():
        return {"nav_pages": [(label, endpoint) for _bp, label, endpoint in PAGES]}

    @app.get("/")
    def home():
        return redirect(url_for("jobs.index"))

    return app
