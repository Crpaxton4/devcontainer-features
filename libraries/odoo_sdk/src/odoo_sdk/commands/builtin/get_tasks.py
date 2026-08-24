from typing import Any, Dict, List, Optional, Tuple

from odoo_sdk.utilities.odoo_helpers import get_task_detail

from ..command import Command
from ._registration import builtin_command


@builtin_command
class GetTasksCommand(Command):
    """List project tasks, optionally narrowed by an Odoo domain filter."""

    _name = "get_tasks"
    _description = (
        "Lists project tasks with an optional domain filter. Per-task detail "
        "is opt-in via ``include``, mirroring get_task's contract: a list of "
        "any of 'description' (task body as Markdown), 'dependencies' "
        "(blocked_by + blocks tasks), 'timesheets' (logged time entries), "
        "'subtasks' (child tasks) — applied to each result, so a task list "
        "with descriptions costs one call instead of one get_task per task. "
        "When ``include`` is omitted only the cheap summary fields are "
        "returned."
    )

    def execute(
        self,
        domain: Optional[List[Tuple[str, str, Any]]] = None,
        limit: int = 10,
        include: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Search ``project.task`` and return summary fields for each match.

        When ``include`` is provided, each match is expanded through the same
        detail fetch that backs ``get_task`` (:func:`get_task_detail`), so
        e.g. ``include=["description"]`` returns descriptions for the whole
        batch in one call. When ``include`` is omitted the cheap summary-only
        behavior is unchanged.
        """
        task_domain = domain or []
        matches = self._client["project.task"].search(task_domain, limit=limit)
        if include is not None:
            details = (
                get_task_detail(self._client, task_id, include=include)
                for task_id in matches.ids
            )
            # A task deleted between the search and the per-task read yields
            # None; drop it rather than surfacing holes in the batch.
            return [detail for detail in details if detail is not None]
        fields_to_fetch = [
            "name",
            "project_id",
            "stage_id",
            "user_ids",
            "date_deadline",
        ]
        return matches.read(fields_to_fetch)
