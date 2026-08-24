from typing import Any

from ..command import Command
from ._registration import builtin_command
from odoo_sdk.state import TaskRun
from odoo_sdk.utilities.env import assert_odoo_devcontainer
from odoo_sdk.utilities.odoo_helpers import count_chatter_messages_after


@builtin_command
class TaskStatusCommand(Command):
    """Return all active task tracking sessions with elapsed time.

    Not repo-scoped: ``get_all_active_runs()`` has no repo predicate and the
    shared state store (#388) has no repo column, so sessions started from any
    repository are returned.
    """

    _name = "task_status"
    _description = (
        "Show all actively tracked tasks (RUNNING or AWAITING_ANSWERS) "
        "with elapsed time. A session that posted a question also reports "
        "new_messages_since_question: how many chatter messages arrived after "
        "the question (0 = still unanswered), so a clarify -> wait -> rerun "
        "loop can poll deterministically. Results are not scoped to a "
        "repository: the shared state store has no repo column, so every "
        "active run is returned."
    )

    def execute(self) -> list[dict[str, Any]]:
        """Return active sessions with computed elapsed time.

        :return: List of active session dicts.
        """
        assert_odoo_devcontainer()
        db = self.state
        runs = db.get_all_active_runs()
        return [self._entry(run) for run in runs]

    def _entry(self, run: TaskRun) -> dict[str, Any]:
        """Shape one active run, adding the answer count when a question is out.

        ``new_messages_since_question`` (#625) appears only on runs whose
        ``question_message_id`` watermark is stamped — one ``search_count`` per
        such run, nothing for the common no-question case — counting the chatter
        messages newer than the run's most recent posted question.
        """
        entry: dict[str, Any] = {
            "run_id": run.id,
            "task_id": run.task_id,
            "task_name": run.task_name,
            "project_name": run.project_name,
            "state": run.state.value,
            "started_at": run.started_at.isoformat(),
            "elapsed": run.elapsed_human,
        }
        if run.question_message_id is not None:
            entry["new_messages_since_question"] = count_chatter_messages_after(
                self._client, run.task_id, run.question_message_id
            )
        return entry
