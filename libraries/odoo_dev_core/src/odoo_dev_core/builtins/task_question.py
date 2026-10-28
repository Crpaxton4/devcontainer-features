"""Post a question to a task's chatter."""

from dataclasses import dataclass
from typing import Any, Optional

from odoo_dev_core.services.odoo_helpers import get_task_detail, post_chatter_note

from ..ports import Services
from ..registry import command
from ._chatter import MAX_CHATTER_BODY_CHARS, enforce_chatter_body_limit


@dataclass(frozen=True)
class Args:
    task_id: int
    question: str
    dedupe_key: Optional[str] = None


@command(
    "task_question",
    args=Args,
    description=(
        "Post a question (prefixed with [?]) to the Odoo task chatter. The "
        f"question is limited to {MAX_CHATTER_BODY_CHARS} characters (longer "
        "questions are rejected, not truncated), so keep it simple, direct, "
        "and plain. Pass an optional 'dedupe_key' to make "
        "the call idempotent: a retried call with a key already seen for this "
        "task skips the post and returns the existing message id."
    ),
)
def handle(services: Services, args: Args) -> dict[str, Any]:
    """Post a question to the task chatter.

    When ``ODOO_JOB_ID`` is set in ``services.env`` and a job ledger is
    available, the dispatcher job's question counter is incremented after the
    post and the result carries ``job_id``.

    :param task_id: Odoo project.task record id.
    :param question: Question text to post (max ``MAX_CHATTER_BODY_CHARS``
        chars).
    :param dedupe_key: Optional idempotency key (#631). A key already seen
        for this task short-circuits BEFORE any side effect - no chatter
        post - and returns the message id the first call produced with
        ``deduplicated: True``. Omitted: behavior unchanged.
    :return: Confirmation with message id.
    :raises ValueError: When the task does not exist or the question is
        over the chatter limit.
    """
    enforce_chatter_body_limit(args.question, "question")
    task = get_task_detail(services.client, args.task_id, include=[])
    if task is None:
        raise ValueError(f"Task {args.task_id} not found.")
    db = services.events

    if args.dedupe_key is not None:
        existing_message_id = db.get_chatter_dedupe(args.task_id, args.dedupe_key)
        if existing_message_id is not None:
            return {
                "task_name": task["name"],
                "message_id": existing_message_id,
                "question": args.question,
                "deduplicated": True,
            }

    message_id = post_chatter_note(
        services.client, args.task_id, f"[?] {args.question}"
    )

    result: dict[str, Any] = {
        "task_name": task["name"],
        "message_id": message_id,
        "question": args.question,
    }
    if args.dedupe_key is not None:
        db.record_chatter_dedupe(args.task_id, args.dedupe_key, message_id)
        result["deduplicated"] = False
    job_id = services.env.get("ODOO_JOB_ID")
    if job_id and services.jobs is not None:
        services.jobs.increment_questions(job_id)
        result["job_id"] = job_id
    return result
