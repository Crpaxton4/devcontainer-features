from typing import Any, Optional

from ..command import (
    MAX_CHATTER_BODY_CHARS,
    Command,
    enforce_chatter_body_limit,
)
from ._registration import builtin_command
from odoo_client.services.odoo_helpers import get_task_detail, post_chatter_note


@builtin_command
class TaskQuestionCommand(Command):
    """Post a question to a task's chatter."""

    _name = "task_question"
    _description = (
        "Post a question (prefixed with [?]) to the Odoo task chatter. The "
        f"question is limited to {MAX_CHATTER_BODY_CHARS} characters (longer "
        "questions are rejected, not truncated), so keep it simple, direct, "
        "and plain. Pass an optional 'dedupe_key' to make "
        "the call idempotent: a retried call with a key already seen for this "
        "task skips the post and returns the existing message id."
    )

    def execute(
        self, task_id: int, question: str, dedupe_key: Optional[str] = None
    ) -> dict[str, Any]:
        """Post a question to the task chatter.

        :param task_id: Odoo project.task record id.
        :param question: Question text to post (max ``MAX_CHATTER_BODY_CHARS``
            chars).
        :param dedupe_key: Optional idempotency key (#631). A key already seen
            for this task short-circuits BEFORE any side effect — no chatter
            post — and returns the message id the first call produced with
            ``deduplicated: True``. Omitted: behavior unchanged.
        :return: Confirmation with message id.
        :raises ValueError: When the task does not exist or the question is
            over the chatter limit.
        """
        enforce_chatter_body_limit(question, "question")
        task = get_task_detail(self._client, task_id, include=[])
        if task is None:
            raise ValueError(f"Task {task_id} not found.")
        db = self.state

        if dedupe_key is not None:
            existing_message_id = db.get_chatter_dedupe(task_id, dedupe_key)
            if existing_message_id is not None:
                return {
                    "task_name": task["name"],
                    "message_id": existing_message_id,
                    "question": question,
                    "deduplicated": True,
                }

        message_id = post_chatter_note(self._client, task_id, f"[?] {question}")

        result: dict[str, Any] = {
            "task_name": task["name"],
            "message_id": message_id,
            "question": question,
        }
        if dedupe_key is not None:
            db.record_chatter_dedupe(task_id, dedupe_key, message_id)
            result["deduplicated"] = False
        return result
