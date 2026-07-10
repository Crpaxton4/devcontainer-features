from typing import Any

from ..command import Command
from odoo_sdk.utilities.env import assert_odoo_devcontainer
from odoo_sdk.utilities.odoo_helpers import post_chatter_note
from odoo_sdk.utilities.timesheet import emit_agent_event
from odoo_sdk.state import LocalStateClient as TaskStateDB


class TaskNoteCommand(Command):
    """Post a note to a task's chatter and record it in the local session."""

    _name = "task_note"
    _description = (
        "Post a free-form note to the Odoo task chatter and append it to "
        "the local session log. Requires an active tracking session."
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
        emit_agent_event(db, task_id, f"task_note: {note}")
        return {
            "task_name": run.task_name,
            "message_id": message_id,
            "note": note,
        }
