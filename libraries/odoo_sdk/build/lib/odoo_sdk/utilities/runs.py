"""Presentation helpers shared by the run-listing commands.

The ``list_runs`` and ``report_runs`` built-in commands both surface tracker
:class:`~odoo_sdk.state.models.TaskRun` rows as plain, JSON-serializable dicts so
every frontend (CLI table, MCP tool wire schema, TUI) formats the same shape.
Keeping the projection here avoids the two commands importing each other.
"""

from typing import Any

from odoo_sdk.state import TaskRun


def run_summary(run: TaskRun) -> dict[str, Any]:
    """Project one :class:`TaskRun` onto the fields the run tables display.

    :param run: The tracker run to summarize.
    :type run: TaskRun
    :returns: A dict with the run's SQLite ``id``, Odoo ``task_id``, task and
        project names, FSM ``state`` value, and human-readable ``elapsed`` time.
    :rtype: dict[str, Any]
    """
    return {
        "id": run.id,
        "task_id": run.task_id,
        "task_name": run.task_name,
        "project_name": run.project_name,
        "state": run.state.value,
        "elapsed": run.elapsed_human,
    }
