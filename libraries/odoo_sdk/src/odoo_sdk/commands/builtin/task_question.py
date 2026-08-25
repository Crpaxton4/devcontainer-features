from typing import Any, Optional

from ..command import (
    MAX_CHATTER_BODY_CHARS,
    Command,
    enforce_chatter_body_limit,
    require_active_run,
)
from ._registration import builtin_command
from odoo_sdk.utilities.env import assert_sdk_configured
from odoo_sdk.utilities.odoo_helpers import post_chatter_note
from odoo_sdk.state import TaskState


@builtin_command
class TaskQuestionCommand(Command):
    """Post a question to a task's chatter and transition to AWAITING_ANSWERS."""

    _name = "task_question"
    _description = (
        "Post a question (prefixed with [?]) to the Odoo task chatter. The "
        f"question is limited to {MAX_CHATTER_BODY_CHARS} characters (longer "
        "questions are rejected, not truncated), so keep it simple, direct, "
        "and plain. Transitions the session from RUNNING to AWAITING_ANSWERS. "
        "Multiple questions are allowed (self-loop on AWAITING_ANSWERS). The "
        "posted message id is recorded as an answer watermark: task_status "
        "reports how many chatter messages arrived after it "
        "(new_messages_since_question). Pass an optional 'dedupe_key' to make "
        "the call idempotent: a retried call with a key already seen for this "
        "task skips the post and returns the existing message id."
    )

    def execute(
        self, task_id: int, question: str, dedupe_key: Optional[str] = None
    ) -> dict[str, Any]:
        """Post a question, stamp the answer watermark, and update session state.

        :param task_id: Odoo project.task record id.
        :param question: Question text to post (max ``MAX_CHATTER_BODY_CHARS``
            chars).
        :param dedupe_key: Optional idempotency key (#631). A key already seen
            for this task short-circuits BEFORE any side effect — no chatter
            post, no watermark, no state transition — and returns the message
            id the first call produced with ``deduplicated: True``. Omitted:
            behavior unchanged.
        :return: Confirmation with message id and new state.
        """
        assert_sdk_configured()
        enforce_chatter_body_limit(question, "question")
        db = self.state
        run = require_active_run(db, task_id)

        if dedupe_key is not None:
            existing_message_id = db.get_chatter_dedupe(task_id, dedupe_key)
            if existing_message_id is not None:
                return {
                    "task_name": run.task_name,
                    "message_id": existing_message_id,
                    "question": question,
                    "state": run.state.value,
                    "deduplicated": True,
                }

        body = f"[?] {question}"
        message_id = post_chatter_note(self._client, task_id, body)

        # The watermark (#625) is stamped before the state transition, while the
        # run is still guaranteed active; a session stopped mid-call raises here
        # — detectably — mirroring task_note's post-then-record tradeoff.
        db.set_question_watermark(task_id, message_id)

        if run.state == TaskState.RUNNING:
            run = db.transition_to_awaiting(task_id)

        result: dict[str, Any] = {
            "task_name": run.task_name,
            "message_id": message_id,
            "question": question,
            "state": run.state.value,
        }
        if dedupe_key is not None:
            db.record_chatter_dedupe(task_id, dedupe_key, message_id)
            result["deduplicated"] = False
        return result
