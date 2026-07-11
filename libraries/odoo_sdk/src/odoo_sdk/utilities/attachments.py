"""Odoo API helpers for reading a task's attachments.

An agent can read a task's body and chatter text but has no path to the binary
documents attached to it (screenshots, PDFs, CSVs). This module is the single
owner of that read path: it lists a ``project.task``'s attachments from **both**
sources they live in and returns them as flat metadata dicts, with the raw
bytes gated behind an opt-in flag so the default call stays cheap.

The two sources:

* ``ir.attachment`` records linked directly to the task
  (``res_model="project.task"``, ``res_id=task_id``) — ``source="task"``.
* ``mail.message`` chatter attachments reached via ``attachment_ids`` — those
  carry ``source="message"``.

An attachment can appear in both (a chatter attachment is still an
``ir.attachment`` linked to the task), so results are **deduped by attachment
id**; the direct-task query is read first, so a shared attachment keeps its
``source="task"`` label.

The design mirrors the opt-in "expensive detail" convention of
:func:`odoo_sdk.utilities.odoo_helpers.get_task_detail` (its ``include``): the
default omits the raw ``datas`` bytes and ``include_content=True`` opts into
the base64 payload, so listing attachments never drags their contents over the
wire unless asked.
"""

from typing import Any

from odoo_sdk.client import OdooClient

# Metadata read for every attachment. ``datas`` (the base64 raw bytes) is added
# to this list only when ``include_content`` is set, so the default stays light.
_ATTACHMENT_METADATA_FIELDS = [
    "name",
    "mimetype",
    "file_size",
    "create_date",
]


def _attachment_fields(include_content: bool) -> list[str]:
    """Return the ``ir.attachment`` fields to read for the requested payload."""
    fields = list(_ATTACHMENT_METADATA_FIELDS)
    if include_content:
        fields.append("datas")
    return fields


def _to_result(record: dict, source: str, include_content: bool) -> dict[str, Any]:
    """Shape one raw ``ir.attachment`` record into a flat result dict."""
    result: dict[str, Any] = {
        "id": record["id"],
        "name": record.get("name"),
        "mimetype": record.get("mimetype"),
        "file_size": record.get("file_size"),
        "create_date": record.get("create_date"),
        "source": source,
    }
    if include_content:
        result["datas"] = record.get("datas")
    return result


def _direct_task_attachments(
    client: OdooClient, task_id: int, include_content: bool
) -> list[dict]:
    """Read ``ir.attachment`` records linked directly to the task."""
    return client.execute(
        "ir.attachment",
        "search_read",
        [("res_model", "=", "project.task"), ("res_id", "=", task_id)],
        fields=_attachment_fields(include_content),
    )


def _message_attachment_ids(client: OdooClient, task_id: int) -> list[int]:
    """Collect the ids of every chatter attachment on the task, order preserved."""
    messages = client.execute(
        "mail.message",
        "search_read",
        [("model", "=", "project.task"), ("res_id", "=", task_id)],
        fields=["id", "attachment_ids"],
    )
    ids: list[int] = []
    for message in messages:
        for attachment_id in message.get("attachment_ids") or []:
            ids.append(attachment_id)
    return ids


def _message_attachments(
    client: OdooClient, attachment_ids: list[int], include_content: bool
) -> list[dict]:
    """Read the ``ir.attachment`` records for the collected chatter ids."""
    if not attachment_ids:
        return []
    return client.execute(
        "ir.attachment",
        "read",
        attachment_ids,
        fields=_attachment_fields(include_content),
    )


def get_task_attachments(
    client: OdooClient, task_id: int, include_content: bool = False
) -> list[dict]:
    """List a task's attachments from both the task and its chatter.

    Attachments are gathered from two sources — ``ir.attachment`` records linked
    directly to the ``project.task`` (``source="task"``) and the chatter
    attachments reached via ``mail.message.attachment_ids`` (``source="message"``)
    — then **deduped by attachment id**. The direct-task source is read first, so
    an attachment present in both keeps its ``source="task"`` label.

    Each result always carries the metadata ``id``, ``name``, ``mimetype``,
    ``file_size``, ``create_date`` and ``source``. The raw bytes are opt-in:
    with the default ``include_content=False`` the base64 ``datas`` payload is
    omitted so the call stays cheap; ``include_content=True`` adds ``datas`` to
    each result.

    :param client: The Odoo API client.
    :type client: OdooClient
    :param task_id: The ``project.task`` id whose attachments are listed.
    :type task_id: int
    :param include_content: When ``True``, include the base64 ``datas`` bytes of
        each attachment. Defaults to ``False`` (metadata only).
    :type include_content: bool
    :return: One flat dict per distinct attachment, task-linked ones first.
    :rtype: list[dict]
    """
    results: list[dict] = []
    seen: set[int] = set()

    def _collect(records: list[dict], source: str) -> None:
        for record in records:
            attachment_id = record["id"]
            if attachment_id in seen:
                continue
            seen.add(attachment_id)
            results.append(_to_result(record, source, include_content))

    _collect(
        _direct_task_attachments(client, task_id, include_content),
        "task",
    )
    message_ids = _message_attachment_ids(client, task_id)
    _collect(
        _message_attachments(client, message_ids, include_content),
        "message",
    )
    return results
