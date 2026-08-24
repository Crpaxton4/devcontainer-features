from typing import Any, Optional

from ..command import (
    MAX_CHATTER_BODY_CHARS,
    Command,
    enforce_chatter_body_limit,
    require_active_run,
)
from ._registration import builtin_command
from odoo_sdk.utilities.attachments import create_attachments
from odoo_sdk.utilities.checkpoint import checkpoint_hint
from odoo_sdk.utilities.env import assert_odoo_devcontainer
from odoo_sdk.utilities.odoo_helpers import post_chatter_note


@builtin_command
class TaskNoteCommand(Command):
    """Post a note to a task's chatter and record it in the local session."""

    _name = "task_note"
    _description = (
        "Post a progress note to the Odoo task chatter and append it to the "
        "local session log. The note is written in Markdown and rendered to "
        "HTML for the chatter, and is limited to "
        f"{MAX_CHATTER_BODY_CHARS} characters (longer notes are rejected, "
        "not truncated), so keep it simple, direct, and plain. Files can be "
        "attached to the posted message via 'attachments': a list of file "
        "specs, each either {'path': <local file path>} or "
        "{'content': <base64 bytes>, 'name': <filename>} with an optional "
        "'mimetype'. Requires an active tracking session."
    )

    def execute(
        self,
        task_id: int,
        note: str,
        attachments: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """Post a chatter note (optionally with attachments) and record it locally.

        :param task_id: Odoo project.task record id.
        :param note: Note text to post (max ``MAX_CHATTER_BODY_CHARS`` chars).
        :param attachments: Optional list of file specs (``path`` or
            ``content`` + ``name``, optional ``mimetype``) uploaded as
            ``ir.attachment`` records and linked to the posted message (#604).
        :return: Confirmation with message id (and attachment ids when files
            were attached).
        """
        assert_odoo_devcontainer()
        enforce_chatter_body_limit(note, "note")
        db = self.state
        run = require_active_run(db, task_id)

        # Attachments are created linked to the task (res_model/res_id) so they
        # appear on the record, then handed to ``message_post`` so the chatter
        # message carries them too (#604). Specs are validated before any
        # record is created, so a malformed spec never posts a partial note.
        attachment_ids: Optional[list[int]] = None
        if attachments:
            attachment_ids = create_attachments(
                self._client,
                attachments,
                res_model="project.task",
                res_id=task_id,
            )

        message_id = post_chatter_note(
            self._client, task_id, note, attachment_ids=attachment_ids
        )
        db.append_note(task_id, note)
        # The MCP wrapper records THIS call's ``task_note`` event only after the
        # command returns, so the hint reads the gap since the *previous* note
        # (or the run start) — exactly the cadence signal #387 asks for.
        result: dict[str, Any] = {
            "task_name": run.task_name,
            "message_id": message_id,
            "note": note,
        }
        if attachment_ids:
            result["attachment_ids"] = attachment_ids
        result.update(checkpoint_hint(db, task_id, run.started_at))
        return result
