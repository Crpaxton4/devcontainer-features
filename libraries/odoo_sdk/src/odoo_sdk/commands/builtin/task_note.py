from typing import Any, Optional

from ..command import (
    MAX_CHATTER_BODY_CHARS,
    Command,
    enforce_chatter_body_limit,
    require_active_run,
)
from ._registration import builtin_command
from odoo_sdk.services.attachments import create_attachments
from odoo_sdk.tracking.checkpoint import checkpoint_hint
from odoo_sdk.tracking.env import assert_sdk_configured
from odoo_sdk.services.odoo_helpers import post_chatter_note


@builtin_command
class TaskNoteCommand(Command):
    """Post a note to a task's chatter and record it in the local session.

    With ``interim=True`` (#901) only the local record happens: the chatter
    post — and the follower notification it fires — is skipped entirely.
    """

    _name = "task_note"
    _description = (
        "Post a progress note to the Odoo task chatter and append it to the "
        "local session log. The note is written in Markdown and rendered to "
        "HTML for the chatter, and is limited to "
        f"{MAX_CHATTER_BODY_CHARS} characters (longer notes are rejected, "
        "not truncated), so keep it simple, direct, and plain. Chatter notes "
        "notify every follower on the task, so post ONE consolidated note per "
        "run, just before stopping. To capture a plan or a checkpoint mid-run, "
        "pass 'interim': true — that appends to the local session log ONLY: "
        "nothing is posted to the chatter, no attachments are accepted, no "
        "dedupe key applies, and the character limit does not apply. Interim "
        "notes still reach the run summary derived at stop_task, so detail "
        "captured this way is not lost. Files can be "
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
        "skips the post and returns the existing message id. Requires an "
        "active tracking session."
    )

    def execute(
        self,
        task_id: int,
        note: str,
        attachments: Optional[list[dict[str, Any]]] = None,
        dedupe_key: Optional[str] = None,
        interim: bool = False,
    ) -> dict[str, Any]:
        """Record a note locally, then post it (with optional attachments) to chatter.

        The local append commits before the Odoo post (#627): a note that made
        it to the chatter is guaranteed to be in the local session log, and a
        session stopped between the guard and the append raises before any
        chatter post happens.

        :param task_id: Odoo project.task record id.
        :param note: Note text to post (max ``MAX_CHATTER_BODY_CHARS`` chars;
            unbounded when ``interim`` is set, since nothing is posted).
        :param attachments: Optional list of file specs (``path`` or
            ``content`` + ``name``, optional ``mimetype``) uploaded as
            ``ir.attachment`` records and linked to the posted message (#604).
            The chatter is client-visible and nothing in the toolset unlinks
            an attachment afterwards (#767), so pass deliverables only — never
            scripts, logs, or internal analysis.
        :param dedupe_key: Optional idempotency key (#631). A key already seen
            for this task short-circuits BEFORE any side effect — no attachment
            upload, no local append, no chatter post — and returns the message
            id the first call produced with ``deduplicated: True``. Omitted:
            behavior unchanged. Ignored when ``interim`` is set: an interim
            note has no chatter post to deduplicate.
        :param interim: Capture the note in the local session log ONLY (#901).
            No chatter post, so no follower notification, no attachments
            (passing them raises :class:`ValueError`), no dedupe key, and no
            length cap — the body is internal/local text like a derived run
            summary. The appended note still feeds the run summary
            ``stop_task`` derives, so an interim checkpoint survives the run
            without spending a client-visible message on it. Default
            ``False``: the chatter post is unchanged.
        :return: Confirmation with message id (and attachment ids when files
            were attached), or ``{"interim": True, ...}`` when ``interim`` is
            set and nothing was posted.
        """
        assert_sdk_configured()
        # Interim notes never reach Odoo, so they take the internal/local text
        # policy (#626): no chatter cap, no attachments, no dedupe. The local
        # append is the SAME one the posting path performs, which is what puts
        # the checkpoint into the derived run summary.
        if interim:
            if attachments:
                raise ValueError(
                    "attachments are not supported with interim=True: an "
                    "interim note is never posted to the chatter, so there is "
                    "no message to attach files to. Post a non-interim note "
                    "to deliver files."
                )
            db = self.state
            require_active_run(db, task_id)
            db.append_note(task_id, note)
            return {
                "interim": True,
                "task_id": task_id,
                "note_len": len(note),
            }

        enforce_chatter_body_limit(note, "note")
        db = self.state
        run = require_active_run(db, task_id)

        # Dedupe (#631) is checked FIRST — ahead of attachment creation, the
        # local append, and the chatter post — so a replayed call has no side
        # effect anywhere: it only returns the reference the first call made.
        if dedupe_key is not None:
            existing_message_id = db.get_chatter_dedupe(task_id, dedupe_key)
            if existing_message_id is not None:
                return {
                    "task_name": run.task_name,
                    "message_id": existing_message_id,
                    "note": note,
                    "deduplicated": True,
                }

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

        # Ordering (#627): the local append COMMITS before the chatter post, so
        # a note visible in Odoo always implies the note is present locally.
        # ``append_note`` re-checks the session inside its own single UPDATE, so
        # a session stopped after the guard above fails HERE — detectably,
        # before anything reaches the chatter. The inverse failure (local note
        # recorded, then the post raises) surfaces the post error to the caller
        # to retry; a duplicate local note on retry is benign, while a chatter
        # note missing from the session log would silently corrupt it.
        db.append_note(task_id, note)
        message_id = post_chatter_note(
            self._client, task_id, note, attachment_ids=attachment_ids
        )
        # The MCP wrapper records THIS call's ``task_note`` event only after the
        # command returns, so the hint reads the gap since the *previous* note
        # (or the run start) — exactly the cadence signal #387 asks for.
        result: dict[str, Any] = {
            "task_name": run.task_name,
            "message_id": message_id,
            "note": note,
        }
        if dedupe_key is not None:
            # Recorded only AFTER a successful post: a failed post must stay
            # retryable under the same key rather than claiming it for nothing.
            db.record_chatter_dedupe(task_id, dedupe_key, message_id)
            result["deduplicated"] = False
        if attachment_ids:
            result["attachment_ids"] = attachment_ids
        result.update(checkpoint_hint(db, task_id, run.started_at))
        return result
