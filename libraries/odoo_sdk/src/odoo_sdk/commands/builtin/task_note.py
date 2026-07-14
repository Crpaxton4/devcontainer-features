from typing import Any

from ..command import Command
from ._registration import builtin_command
from odoo_sdk.utilities.env import assert_odoo_devcontainer
from odoo_sdk.utilities.odoo_helpers import post_chatter_note
from odoo_sdk.state import LocalStateClient as TaskStateDB


@builtin_command
class TaskNoteCommand(Command):
    """Post a note to a task's chatter and record it in the local session."""

    _name = "task_note"
    _description = (
        "Post a progress note to the Odoo task chatter and append it to the "
        "local session log. The note is written in Markdown and rendered to "
        "HTML for the chatter, so keep it short and scannable: a one-line "
        "summary followed by 2-4 short bullets, not long free-form prose. "
        "Requires an active tracking session."
    )

    def execute(self, task_id: int, note: str) -> dict[str, Any]:
        """Post a chatter note and record it locally.

        :param task_id: Odoo project.task record id.
        :param note: Note text to post.
        :return: Confirmation with message id.
        """
        assert_odoo_devcontainer()
        db = TaskStateDB()
        run = db.get_active_run(task_id)
        if run is None:
            from odoo_sdk.state import TaskNotRunningError
            raise TaskNotRunningError(f"No active session for task {task_id}.")

        message_id = post_chatter_note(self._client, task_id, note)
        db.append_note(task_id, note)
        return {
            "task_name": run.task_name,
            "message_id": message_id,
            "note": note,
        }
