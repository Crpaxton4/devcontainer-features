"""Post a note to a task's chatter."""

from dataclasses import dataclass
from typing import Any, Optional
from ._chatter import MAX_CHATTER_BODY_CHARS, enforce_chatter_body_limit
from odoo_dev_core.services.attachments import create_attachments
from odoo_dev_core.services.odoo_helpers import get_task_detail, post_chatter_note

from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    task_id: int
    note: str
    attachments: Optional[list[dict[str, Any]]] = None
    dedupe_key: Optional[str] = None


@command(
    "task_note",
    args=Args,
    description="Post a progress note to the Odoo task chatter. The note is written in "
    "Markdown and rendered to HTML for the chatter, and is limited to "
    f"{MAX_CHATTER_BODY_CHARS} characters (longer notes are rejected, "
    "not truncated), so keep it simple, direct, and plain. Chatter notes "
    "notify every follower on the task, so post ONE consolidated note per "
    "run, just before stopping. Files can be "
    "attached to the posted message via 'attachments': a list of file "
    "specs, each either {'path': <local file path>} or "
    "{'content': <base64 bytes>, 'name': <filename>} with an optional "
    "'mimetype'. Task chatter is CLIENT-VISIBLE, and there is no tool "
    "that removes a note or unlinks an attachment once posted, so attach "
    "only deliverables the client asked to receive. Do NOT attach "
    "internal engineering material — scripts, logs, test or benchmark "
    "output, tracebacks, machine paths, or working analysis. That "
    "evidence belongs in the pull request, the commit history, or an "
    "internal channel; link to it from the note instead of attaching it. "
    "Pass an optional 'dedupe_key' to make the call "
    "idempotent: a retried call with a key already seen for this task "
    "skips the post and returns the existing message id.",
)
def handle(services: Services, args: Args) -> dict[str, Any]:
    """Post a note (with optional attachments) to the task chatter.

    :param task_id: Odoo project.task record id.
    :param note: Note text to post (max ``MAX_CHATTER_BODY_CHARS`` chars).
    :param attachments: Optional list of file specs (``path`` or
        ``content`` + ``name``, optional ``mimetype``) uploaded as
        ``ir.attachment`` records and linked to the posted message (#604).
        The chatter is client-visible and nothing in the toolset unlinks
        an attachment afterwards (#767), so pass deliverables only — never
        scripts, logs, or internal analysis.
    :param dedupe_key: Optional idempotency key (#631). A key already seen
        for this task short-circuits BEFORE any side effect — no attachment
        upload, no chatter post — and returns the message id the first call
        produced with ``deduplicated: True``. Omitted: behavior unchanged.
    :return: Confirmation with message id (and attachment ids when files
        were attached).
    :raises ValueError: When the task does not exist or the note is over
        the chatter limit.
    """
    enforce_chatter_body_limit(args.note, "note")
    task = get_task_detail(services.client, args.task_id, include=[])
    if task is None:
        raise ValueError(f"Task {args.task_id} not found.")
    db = services.events

    # Dedupe (#631) is checked FIRST — ahead of attachment creation and the
    # chatter post — so a replayed call has no side effect anywhere: it only
    # returns the reference the first call made.
    if args.dedupe_key is not None:
        existing_message_id = db.get_chatter_dedupe(args.task_id, args.dedupe_key)
        if existing_message_id is not None:
            return {
                "task_name": task["name"],
                "message_id": existing_message_id,
                "note": args.note,
                "deduplicated": True,
            }

    # Attachments are created linked to the task (res_model/res_id) so they
    # appear on the record, then handed to ``message_post`` so the chatter
    # message carries them too (#604). Specs are validated before any
    # record is created, so a malformed spec never posts a partial note.
    attachment_ids: Optional[list[int]] = None
    if args.attachments:
        attachment_ids = create_attachments(
            services.client,
            args.attachments,
            res_model="project.task",
            res_id=args.task_id,
        )

    message_id = post_chatter_note(
        services.client, args.task_id, args.note, attachment_ids=attachment_ids
    )
    result: dict[str, Any] = {
        "task_name": task["name"],
        "message_id": message_id,
        "note": args.note,
    }
    if args.dedupe_key is not None:
        # Recorded only AFTER a successful post: a failed post must stay
        # retryable under the same key rather than claiming it for nothing.
        db.record_chatter_dedupe(args.task_id, args.dedupe_key, message_id)
        result["deduplicated"] = False
    if attachment_ids:
        result["attachment_ids"] = attachment_ids
    return result
