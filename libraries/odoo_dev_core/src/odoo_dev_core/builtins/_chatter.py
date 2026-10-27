"""Chatter body limit shared by the chatter-posting builtins."""

#: Maximum characters allowed in a chatter message/note body posted by the SDK
#: (#610). Enforced at the command layer — transport-agnostic per ADR-004 — so
#: every consumer (MCP tools, CLI, library callers) inherits the same cap.
#:
#: Scope (maintainer decision, #626): this cap applies ONLY to chatter bodies
#: posted to Odoo (``task_note`` / ``task_question``). Internal/local text —
#: event payloads and timesheet entry names, which never reach the
#: chatter — carry NO length limit and must never be routed through
#: :func:`enforce_chatter_body_limit`.
#:
#: Raised 300 -> 500 by #901: a run now posts ONE consolidated chatter note
#: instead of a per-checkpoint stream, and 300 characters split a single
#: coherent finding across several posted messages — which was itself a
#: driver of the note volume the issue was filed about.
MAX_CHATTER_BODY_CHARS = 500


def enforce_chatter_body_limit(body: str, label: str) -> None:
    """Reject a chatter ``body`` over :data:`MAX_CHATTER_BODY_CHARS` (#610).

    Shared by the chatter-posting builtin commands (``task_note``,
    ``task_question``). Over-limit content is *rejected* with a
    :class:`ValueError` telling the caller to shorten it — never silently
    truncated, which would post content the caller did not write.

    :param body: The caller-supplied message text to validate.
    :param label: Parameter name used in the error message (e.g. ``"note"``).
    """
    if len(body) > MAX_CHATTER_BODY_CHARS:
        raise ValueError(
            f"{label} is {len(body)} characters, over the "
            f"{MAX_CHATTER_BODY_CHARS}-character limit for posted chatter "
            "content. Shorten the message: keep notes simple, direct, plain."
        )
