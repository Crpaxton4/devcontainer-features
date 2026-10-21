"""The friction files ``friction/<date>.md`` of the state dir and the orchestrator home."""

from flask import Blueprint, Response, abort, render_template

from webui import paths

bp = Blueprint("friction", __name__)


def _sources() -> dict:
    return {
        "state": paths.state_root() / "friction",
        "orchestrator": paths.orchestrator_home() / "friction",
    }


@bp.get("/friction")
def index():
    files = []
    for source, folder in _sources().items():
        if folder.is_dir():
            files += [
                {"source": source, "name": p.name}
                for p in sorted(folder.glob("*.md"), reverse=True)
            ]
    return render_template("friction.html", files=files)


@bp.get("/friction/<source>/<name>")
def show(source: str, name: str):
    folder = _sources().get(source)
    if folder is None or not name.endswith(".md"):
        abort(404)
    root = folder.resolve()
    target = (root / name).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        abort(404)
    return render_template(
        "friction_file.html",
        source=source,
        name=name,
        text=target.read_text(encoding="utf-8", errors="replace"),
    )
