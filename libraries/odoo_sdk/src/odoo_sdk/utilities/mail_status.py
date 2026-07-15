"""Read-only outgoing-mail status helpers (``mail.mail`` / ``mail.message``).

``get_task_chatter`` and friends surface chatter *messages*, but they cannot tell
an agent whether the outbound email a ``message_post`` / ``mail.template`` queued
actually left the outgoing mail queue — nor why it did not. Acceptance criteria
of the form "send an email and verify it was received" therefore had no
first-class MCP read path and forced ad hoc shell scripting against the live DB
(issue #389).

This module joins a record's ``mail.message`` rows to their linked ``mail.mail``
rows and reports each outbound mail's delivery ``state`` (``outgoing`` / ``sent``
/ ``exception`` / ``cancel``), any ``failure_reason`` / ``failure_type``, a
recipients summary, and the message date. It is strictly read-only: it issues
only ``fields_get`` / ``search_read`` / ``read`` and never retries, requeues, or
mutates a mail.

``mail.mail`` is frequently restricted to administrators. A denied read is
translated into one stable, actionable :class:`ValueError` (see
:data:`MAIL_ACCESS_DENIED_MESSAGE`) that the MCP error boundary formats into the
uniform ``{"error": {"type", "message"}}`` payload, so an LLM caller sees a clear
message rather than an opaque access traceback.
"""

from typing import Any

from odoo_sdk.client import OdooClient
from odoo_sdk.transport.errors import OdooAccessError, OdooError

from .odoo_helpers import resolve_many2one

#: Exact, stable error raised when reading ``mail.mail`` is denied. ``mail.mail``
#: (the outgoing mail queue) is commonly admin-restricted, so an
#: :class:`~odoo_sdk.transport.errors.OdooAccessError` on the queue read is
#: converted to this pinned message for the MCP error boundary to format. Pinned
#: so callers and tests can match it verbatim.
MAIL_ACCESS_DENIED_MESSAGE = (
    "Access denied reading mail.mail (the outgoing mail queue). This model is "
    "commonly restricted to administrators; ask an Odoo admin to grant read "
    "access on mail.mail to verify outgoing-mail delivery status."
)

#: ``mail.message`` fields fetched to index a record's messages by id. ``date``
#: and ``subject`` come from the message because ``mail.mail`` does not carry a
#: reliable send timestamp across Odoo versions and often leaves ``subject``
#: blank (inheriting the originating message's subject).
_MESSAGE_FIELDS = ["id", "date", "subject"]

#: ``mail.mail`` fields present on every supported Odoo version, always read.
_MAIL_BASE_FIELDS = [
    "id",
    "mail_message_id",
    "subject",
    "state",
    "email_to",
    "recipient_ids",
]

#: ``mail.mail`` failure fields that are version/edition dependent. Their
#: presence is probed with ``fields_get`` (mirroring the ``unbilled_hours``
#: capability probe) so a field absent on the deployment is simply omitted rather
#: than raising an "Invalid field" fault. ``failure_reason`` exists on modern
#: ``mail.mail``; ``failure_type`` lives on ``mail.notification`` in several
#: versions and is included only where it genuinely exists on ``mail.mail``.
_MAIL_FAILURE_FIELDS = ["failure_reason", "failure_type"]


def _m2o_id(value: Any) -> Any:
    """Return the id from a many2one ``[id, name]`` pair, else the value itself.

    ``resolve_many2one`` extracts the *display name*; this is its id-side twin,
    used to recover the ``mail.message`` id a ``mail.mail`` links to.

    :param value: Raw many2one value (``[id, name]`` pair) or scalar.
    :type value: Any
    :return: The id when a pair is given, otherwise ``value`` unchanged.
    :rtype: Any
    """
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return value[0]
    return value


def _fetch_messages(client: OdooClient, res_model: str, res_id: int) -> list[dict]:
    """Read the ``mail.message`` rows attached to one record.

    :param client: The Odoo API client.
    :type client: OdooClient
    :param res_model: The record's model, e.g. ``"project.task"``.
    :type res_model: str
    :param res_id: The record's id.
    :type res_id: int
    :return: Raw ``mail.message`` rows carrying :data:`_MESSAGE_FIELDS`.
    :rtype: list[dict]
    """
    return client.execute(
        "mail.message",
        "search_read",
        [("model", "=", res_model), ("res_id", "=", res_id)],
        fields=_MESSAGE_FIELDS,
        order="date asc",
    )


def _probe_mail_failure_fields(client: OdooClient) -> list[str]:
    """Return which :data:`_MAIL_FAILURE_FIELDS` actually exist on ``mail.mail``.

    ``fields_get`` returns metadata only for the requested fields that exist, so
    membership of each name in the response is a capability check.

    :param client: The Odoo API client.
    :type client: OdooClient
    :return: The subset of failure fields present on this deployment, in order.
    :rtype: list[str]
    """
    meta = client.execute("mail.mail", "fields_get", _MAIL_FAILURE_FIELDS)
    return [field for field in _MAIL_FAILURE_FIELDS if field in meta]


def _read_mail_rows(
    client: OdooClient, message_ids: list[int], extra_fields: list[str]
) -> list[dict]:
    """Read the ``mail.mail`` rows linked to the given message ids.

    :param client: The Odoo API client.
    :type client: OdooClient
    :param message_ids: ``mail.message`` ids whose linked mails to fetch.
    :type message_ids: list[int]
    :param extra_fields: Probed failure fields to read alongside the base set.
    :type extra_fields: list[str]
    :return: Raw ``mail.mail`` rows, oldest message first.
    :rtype: list[dict]
    """
    return client.execute(
        "mail.mail",
        "search_read",
        [("mail_message_id", "in", message_ids)],
        fields=[*_MAIL_BASE_FIELDS, *extra_fields],
        order="id asc",
    )


def _recipient_names(client: OdooClient, rows: list[dict]) -> dict[int, str]:
    """Resolve every ``recipient_ids`` partner id across ``rows`` to a name.

    A single batched ``res.partner`` read keeps this to one call regardless of
    how many mails/recipients are involved. Read-only and best-effort: if the
    partner read is denied or otherwise fails, an empty map is returned and the
    recipients summary degrades to the raw ``email_to`` addresses rather than
    aborting the whole status report.

    :param client: The Odoo API client.
    :type client: OdooClient
    :param rows: Raw ``mail.mail`` rows carrying ``recipient_ids``.
    :type rows: list[dict]
    :return: Map of partner id to display name (empty when unresolved).
    :rtype: dict[int, str]
    """
    partner_ids = sorted(
        {pid for row in rows for pid in (row.get("recipient_ids") or [])}
    )
    if not partner_ids:
        return {}
    try:
        partners = client.execute(
            "res.partner", "read", partner_ids, fields=["name"]
        )
    except OdooError:
        return {}
    return {p["id"]: p.get("name") or "" for p in partners}


def _summarize_recipients(row: dict, partner_names: dict[int, str]) -> str:
    """Build a de-duplicated, comma-joined recipients string for one mail.

    Combines the raw ``email_to`` addresses with the resolved display names of
    the ``recipient_ids`` partners, preserving order and dropping duplicates and
    blanks.

    :param row: Raw ``mail.mail`` row.
    :type row: dict
    :param partner_names: Partner id to display name map from
        :func:`_recipient_names`.
    :type partner_names: dict[int, str]
    :return: Human-readable recipients summary, ``""`` when none are known.
    :rtype: str
    """
    parts: list[str] = []
    email_to = row.get("email_to") or ""
    parts.extend(chunk.strip() for chunk in email_to.split(","))
    for pid in row.get("recipient_ids") or []:
        parts.append(partner_names.get(pid, ""))
    seen = dict.fromkeys(part for part in parts if part)
    return ", ".join(seen)


def _shape_mail_row(
    row: dict, message_index: dict[int, dict], partner_names: dict[int, str]
) -> dict:
    """Project one raw ``mail.mail`` row into the reported status entry.

    The message id recovered from ``mail_message_id`` looks up the originating
    message's ``date`` and (fallback) ``subject``. Failure fields are surfaced
    only when populated, so a successfully ``sent`` mail carries no failure keys.

    :param row: Raw ``mail.mail`` row.
    :type row: dict
    :param message_index: ``mail.message`` id to raw message row map.
    :type message_index: dict[int, dict]
    :param partner_names: Partner id to display name map.
    :type partner_names: dict[int, str]
    :return: Status entry with ``mail_id``, ``message_id``, ``subject``,
        ``recipients``, ``state``, ``date`` and any populated failure fields.
    :rtype: dict
    """
    message_id = _m2o_id(row.get("mail_message_id"))
    message = message_index.get(message_id, {})
    subject = row.get("subject") or message.get("subject") or ""
    entry = {
        "mail_id": row["id"],
        "message_id": message_id,
        "subject": subject,
        "recipients": _summarize_recipients(row, partner_names),
        "state": row.get("state"),
        "date": message.get("date"),
    }
    for field in _MAIL_FAILURE_FIELDS:
        value = row.get(field)
        if value:
            entry[field] = value
    return entry


def get_mail_status(
    client: OdooClient, res_model: str, res_id: int
) -> list[dict]:
    """Report the outgoing-mail (``mail.mail``) status for one record.

    Finds the record's ``mail.message`` rows (``model`` / ``res_id``), then the
    ``mail.mail`` rows linked to them (``mail_message_id in ...``), and reports
    per outbound mail: ``mail_id``, ``message_id``, ``subject``, a ``recipients``
    summary, the delivery ``state`` (``outgoing`` / ``sent`` / ``exception`` /
    ``cancel``), the message ``date``, and — only when populated —
    ``failure_reason`` / ``failure_type``. Rows are ordered by ``mail.mail`` id
    ascending (outbound-queue creation order).

    Strictly read-only: it never retries, requeues, or cancels a mail. Records
    with only chatter notes (no outbound mail) yield an empty list.

    ``mail.mail`` is commonly admin-restricted; a denied read raises a
    :class:`ValueError` carrying :data:`MAIL_ACCESS_DENIED_MESSAGE` rather than
    surfacing an opaque access fault.

    :param client: The Odoo API client.
    :type client: OdooClient
    :param res_model: The record's model, e.g. ``"project.task"``.
    :type res_model: str
    :param res_id: The record's id.
    :type res_id: int
    :raises ValueError: When reading ``mail.mail`` is denied.
    :return: One status entry per linked ``mail.mail``, oldest first.
    :rtype: list[dict]
    """
    messages = _fetch_messages(client, res_model, res_id)
    if not messages:
        return []
    message_index = {m["id"]: m for m in messages}

    try:
        extra_fields = _probe_mail_failure_fields(client)
        rows = _read_mail_rows(client, list(message_index), extra_fields)
    except OdooAccessError as exc:
        raise ValueError(MAIL_ACCESS_DENIED_MESSAGE) from exc

    partner_names = _recipient_names(client, rows)
    return [_shape_mail_row(row, message_index, partner_names) for row in rows]
