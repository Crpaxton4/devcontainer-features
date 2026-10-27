"""Odoo chatter puller: the user's authored task messages as ``chatter`` events.

``sync_odoo_chatter`` searches ``mail.message`` author-wide over the resolved
window and stores each message keyed ``odoo:mail:<id>``. It talks to Odoo only
through the small :class:`ChatterClient` protocol, so this package does not
depend on the client package.
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime, timezone
from typing import Any, Optional, Protocol

from odoo_dev_data.adapters._common import _run_capture, _window_bounds
from odoo_dev_data.adapters.git import derive_repo_label
from odoo_dev_data.config import LocalConfig
from odoo_dev_data.events import EventRecord, EventStore


class ChatterClient(Protocol):
    """The slice of an Odoo client the chatter puller needs."""

    uid: int

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any: ...


def _current_repo_label(state: EventStore) -> str:
    """Return the current repo's ``owner/repo`` label, or empty string.

    Prefers the identity persisted into the DB (``repo_label``, stamped per #331);
    falls back to deriving it from the ``origin`` remote. Used only by the Odoo
    chatter puller since #652 — the git puller labels each discovered repo from
    its OWN origin via :func:`_repo_label_for`, never this cwd-scoped value.
    """
    label = state.get_setting("repo_label")
    if label:
        return label
    remote = _run_capture(["git", "remote", "get-url", "origin"])
    return derive_repo_label(remote) if remote else ""


def _parse_odoo_dt(value: str) -> datetime:
    """Parse an Odoo naive datetime string, treating it as UTC (Odoo stores UTC)."""
    parsed = datetime.fromisoformat(value.strip().replace(" ", "T"))
    return parsed.replace(tzinfo=timezone.utc)


def _current_partner_id(client: ChatterClient) -> int:
    """Return the ``res.partner`` id backing the authenticated Odoo user.

    :raises RuntimeError: When the authenticated user has no readable
        ``res.users`` record, so the caller degrades to a skip rather than an
        ``IndexError``.
    """
    record = client.execute("res.users", "read", [client.uid], ["partner_id"])
    if not record:
        raise RuntimeError(f"no res.users record for uid {client.uid}")
    partner = record[0]["partner_id"]
    return partner[0] if isinstance(partner, (list, tuple)) else partner


def _odoo_dt_str(moment: datetime) -> str:
    """Format a UTC datetime as Odoo's naive ``YYYY-MM-DD HH:MM:SS`` string."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _search_chatter(
    client: ChatterClient, partner_id: int, since: datetime, until: datetime
) -> list[dict]:
    """Return the user's authored task chatter over ``[since, until]`` (issue #378 #5).

    Author-wide over ALL ``project.task`` messages, NOT just tracked tasks — the
    biggest manual finding was unlogged work on tasks never started locally.

    ``body`` is read alongside ``subject`` (#710): a logged note posts with an
    EMPTY ``subject``, so a chatter event built from the subject alone carried
    no text at all and the billing narrative had nothing of the user's own words
    to lead with. The body supplies that first line (see
    :func:`_chatter_headline`).

    ``message_type``, ``subtype_id``, and ``tracking_value_ids`` are read for
    :func:`_is_tracking_only_message` (#1040): Odoo writes a ``mail.message`` for
    every tracked-field change, so a bare stage flip arrived here looking exactly
    like an authored note and billed as one.
    """
    return client.execute(
        "mail.message",
        "search_read",
        [
            ("model", "=", "project.task"),
            ("author_id", "=", partner_id),
            ("date", ">=", _odoo_dt_str(since)),
            ("date", "<=", _odoo_dt_str(until)),
        ],
        fields=[
            "id",
            "res_id",
            "date",
            "subject",
            "body",
            "message_type",
            "subtype_id",
            "tracking_value_ids",
        ],
    )


# Odoo chatter bodies are HTML. Block-level closers and ``<br>`` become newlines
# FIRST so the note's real first line survives tag-stripping (without this,
# ``<p>a</p><p>b</p>`` would flatten to the single line ``ab``); everything else
# is then dropped and HTML entities unescaped.
#
# NOT ``_text.html_to_markdown``: that renders MARKDOWN (``**bold**``,
# ``[text](url)``) through a MarkItDown stream conversion per call, which is both
# the wrong output — this wants a plain index line, not marked-up text — and a
# disproportionate cost inside a per-message resync loop.
_HTML_LINE_BREAK = re.compile(r"(?i)<br\s*/?>|</(?:p|div|li|tr|h[1-6])\s*>")
_HTML_TAG = re.compile(r"<[^>]*>")

# Cap on the chatter first line stored as the event subject (#710). The note
# itself lives in Odoo and is not being replaced here — this is an index line,
# and an uncapped one is the multi-KB markdown blob #710 was filed about.
_CHATTER_SUBJECT_CHARS = 200


def _chatter_headline(body: Any) -> str:
    """Return the first line of an Odoo chatter HTML body as plain text (#710).

    Best-effort and total: Odoo returns ``False`` for an empty body, and a body
    of pure markup or whitespace has no first line, both of which yield ``""``
    rather than raising on the resync write path.
    """
    if not isinstance(body, str) or not body:
        return ""
    text = html.unescape(_HTML_TAG.sub("", _HTML_LINE_BREAK.sub("\n", body)))
    lines = [stripped for line in text.split("\n") if (stripped := line.strip())]
    return lines[0][:_CHATTER_SUBJECT_CHARS] if lines else ""


#: ``mail.message.message_type`` values Odoo writes for a system-generated
#: record change rather than something a person typed. ``comment`` (a posted
#: note) and ``email`` are deliberately absent.
_NOTIFICATION_MESSAGE_TYPES = frozenset({"notification"})

# A tracking-value rendering: ``Stage: In Progress -> Done`` (Odoo uses a real
# arrow in the chatter HTML; ``->`` covers plain-text variants). Anchored at the
# line start on the tracked field's own label, so an authored note that merely
# MENTIONS a stage never matches.
_STAGE_MOVE_LINE = re.compile(
    r"(?i)^(?:stage|state|kanban state)\s*[:\-]?\s*"
    r"(?:.*(?:→|⇨|->).*|from\s+.+\s+to\s+.+)$"
)


def _is_stage_move_text(headline: str) -> bool:
    """Return True when a chatter first line is only a stage/state move (#1040)."""
    return bool(headline) and bool(_STAGE_MOVE_LINE.match(headline))


def _is_stage_subtype(subtype: Any) -> bool:
    """Return True when a ``subtype_id`` many2one names a stage-change subtype.

    ``search_read`` returns a many2one as ``[id, display_name]``, so the subtype's
    own name (``Task Stage Changed``) is already in hand — no extra RPC. Anything
    else (a bare id, ``False``, absent) is no signal.
    """
    if not isinstance(subtype, (list, tuple)) or len(subtype) < 2:
        return False
    return "stage" in str(subtype[1]).lower()


def _is_tracking_only_message(message: dict) -> bool:
    """Return True when a chatter message records a field change, not work (#1040).

    Odoo writes a ``mail.message`` for every tracked-field write, so an
    administrative stage flip — including the ones made to *correct* timesheets —
    arrived in the puller indistinguishable from an authored note and derived a
    billable session on the task (0.55 h each across six tasks in #1040).

    Two signals, both already on the row:

    * the body is nothing but a tracking-value line (``Stage: Done → In
      Progress``), whatever the message type says; or
    * the message is Odoo's own ``notification`` type, carries tracking values or
      a stage subtype, and has NO author-written body.

    The author-written-body requirement is what keeps this narrow: a person who
    typed something said something, and this must never silently drop real work.
    A free-text note that happens to be administrative (``"Moving out of done to
    correct time sheets"``) is therefore still stored — no small predicate can
    tell it from a progress note, and the TUI review surface is where a human
    declines to bill it.
    """
    headline = _chatter_headline(message.get("body"))
    if _is_stage_move_text(headline):
        return True
    if message.get("message_type") not in _NOTIFICATION_MESSAGE_TYPES:
        return False
    tracked = message.get("tracking_value_ids") or _is_stage_subtype(
        message.get("subtype_id")
    )
    return bool(tracked) and not headline


def _store_message(state: EventStore, message: dict, label: str) -> int:
    """Store one chatter message as a ``chatter`` event; return 1 if inserted.

    A message with no timestamp (Odoo returns ``False`` for an empty datetime) is
    skipped rather than crashing the puller, and so is a tracking-only message
    (:func:`_is_tracking_only_message`, #1040) — a stage flip is a record change,
    not time worked. The task id is the message's ``res_id`` (the task it is ON),
    so it exists by construction. The subject is the message's own when it has
    one, else the body's first line (#710) — the usual case, since a logged note
    carries no subject — so the event arrives carrying the user's own words
    instead of an empty string.
    """
    date = message.get("date")
    if not date or _is_tracking_only_message(message):
        return 0
    res_id = message["res_id"]
    event = EventRecord(
        id=None,
        source="chatter",
        timestamp=_parse_odoo_dt(date),
        task_ids=[str(res_id)],
        repo=label,
        subject=message.get("subject") or _chatter_headline(message.get("body")),
        external_id=f"odoo:mail:{message['id']}",
    )
    return 1 if state.add_event_dedup(event) else 0


def sync_odoo_chatter(
    client: ChatterClient,
    state: EventStore,
    config: Optional[LocalConfig] = None,
    *,
    now: Optional[datetime] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> dict[str, Any]:
    """Reconcile the user's Odoo task chatter into the ``events`` table (issue #378).

    Searches ``mail.message`` author-wide over the resolved window (item 5;
    explicit ``start``/``end`` override the rolling default, #652) and stores
    each as a ``chatter`` event keyed ``odoo:mail:<id>``. Idempotent.
    Returns ``{"inserted": n}``, ``{"skipped": reason}``, or ``{"error": reason}``
    for an empty/inverted window.
    """
    try:
        since, until = _window_bounds(config, now, start, end)
    except ValueError as exc:
        return {"error": str(exc)}
    label = _current_repo_label(state)
    try:
        partner_id = _current_partner_id(client)
        messages = _search_chatter(client, partner_id, since, until)
    except Exception as exc:  # noqa: BLE001
        # This package cannot import the client package, so it cannot name the
        # client's OdooError. Any failure of these two client calls therefore
        # means "Odoo unavailable" and degrades to a skip.
        return {"skipped": f"odoo unavailable: {exc}"}
    inserted = sum(_store_message(state, message, label) for message in messages)
    return {"inserted": inserted}
