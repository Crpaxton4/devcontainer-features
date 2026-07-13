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

import base64
import io
import mimetypes
import os
from typing import Any, Optional

from markitdown import MarkItDown

from odoo_sdk.client import OdooClient
from odoo_sdk.transport.errors import OdooMissingRecordError

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


# --------------------------------------------------------------------------- #
# read_attachment: read one already-stored ir.attachment (read-only).
# --------------------------------------------------------------------------- #

#: The three modes :func:`read_attachment` accepts.
READ_ATTACHMENT_MODES = ("text", "metadata", "raw")

#: Cap (in bytes of the *decoded* payload) applied to the two payload-bearing
#: modes. A single, documented constant so the wire cost of one call is bounded:
#:
#: * ``raw`` refuses a payload larger than this with a ``ValueError`` naming the
#:   size and the cap (base64 of >10 MiB is a poor fit for a tool result).
#: * ``text`` truncates a larger decoded payload to this many bytes *before*
#:   handing it to the converter and flags the result ``truncated: True``.
#:
#: 10 MiB comfortably covers ordinary business documents (PDFs, spreadsheets)
#: while keeping a single call from ballooning the model's context.
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024

#: ``ir.attachment`` metadata fields returned in every mode (never the payload).
_READ_METADATA_FIELDS = [
    "name",
    "mimetype",
    "file_size",
    "res_model",
    "res_id",
    "create_date",
]

#: One shared converter instance; :class:`MarkItDown` is stateless per call and
#: constructing it is not free, so it is built once at import (mirrors
#: :mod:`odoo_sdk.utilities.html`).
_md_converter = MarkItDown()


def _file_extension(filename: Optional[str], mimetype: Optional[str]) -> Optional[str]:
    """Pick a MarkItDown file-extension hint for a payload.

    The attachment ``name``'s own extension is preferred (it is what the user
    uploaded); failing that the ``mimetype`` is mapped to an extension. ``None``
    is returned when neither yields one, leaving MarkItDown to sniff the stream.
    """
    if filename:
        extension = os.path.splitext(filename)[1]
        if extension:
            return extension
    if mimetype:
        return mimetypes.guess_extension(mimetype)
    return None


def _payload_to_markdown(
    payload: bytes, filename: Optional[str], mimetype: Optional[str]
) -> str:
    """Convert a decoded attachment payload to trimmed Markdown via MarkItDown.

    Pure: raw bytes in, Markdown text out, no Odoo I/O. Uses MarkItDown's binary
    ``convert_stream`` path (not the HTML string helper) so PDFs, Office
    documents, CSVs, etc. are rendered to Markdown. The extension hint helps
    MarkItDown route to the right converter.
    """
    extension = _file_extension(filename, mimetype)
    kwargs: dict[str, Any] = {}
    if extension:
        kwargs["file_extension"] = extension
    result = _md_converter.convert_stream(io.BytesIO(payload), **kwargs)
    return result.text_content.strip()


def _attachment_metadata(record: dict, mode: str) -> dict[str, Any]:
    """Shape the always-present metadata for one ``ir.attachment`` record."""
    return {
        "id": record["id"],
        "name": record.get("name"),
        "mimetype": record.get("mimetype"),
        "file_size": record.get("file_size"),
        "res_model": record.get("res_model") or None,
        "res_id": record.get("res_id") or None,
        "create_date": record.get("create_date"),
        "mode": mode,
    }


def _raw_result(
    metadata: dict[str, Any], record: dict, attachment_id: int
) -> dict[str, Any]:
    """Return metadata plus the base64 payload, refusing an oversized one."""
    datas = record.get("datas")
    size = len(base64.b64decode(datas)) if datas else 0
    if size > MAX_ATTACHMENT_BYTES:
        raise ValueError(
            f"Attachment {attachment_id} payload is {size} bytes, over the "
            f"{MAX_ATTACHMENT_BYTES}-byte cap for raw mode. Use mode='metadata' "
            "or mode='text' instead."
        )
    return {**metadata, "datas": datas or None}


def _text_result(metadata: dict[str, Any], record: dict) -> dict[str, Any]:
    """Return metadata plus the Markdown text of the payload.

    The decoded payload is capped at :data:`MAX_ATTACHMENT_BYTES` (flagging
    ``truncated``); any decode/conversion failure degrades to metadata plus a
    ``note`` rather than raising, so an unsupported or unconvertible format is
    never a hard error.
    """
    datas = record.get("datas")
    if not datas:
        return {
            **metadata,
            "text": "",
            "truncated": False,
            "note": "Attachment has no stored binary payload to extract text from.",
        }
    truncated = False
    try:
        payload = base64.b64decode(datas)
        if len(payload) > MAX_ATTACHMENT_BYTES:
            payload = payload[:MAX_ATTACHMENT_BYTES]
            truncated = True
        text = _payload_to_markdown(payload, record.get("name"), record.get("mimetype"))
    except Exception as exc:  # noqa: BLE001 - any backend failure degrades gracefully
        return {
            **metadata,
            "text": "",
            "truncated": truncated,
            "note": (
                f"Could not extract text from {record.get('name')!r} "
                f"(mimetype {record.get('mimetype')!r}): {type(exc).__name__}: {exc}"
            ),
        }
    return {**metadata, "text": text, "truncated": truncated}


def read_attachment(
    client: OdooClient, attachment_id: int, mode: str = "text"
) -> dict[str, Any]:
    """Read one already-stored ``ir.attachment`` from Odoo. Strictly read-only.

    This never uploads or attaches anything; it only reads a document that is
    already in Odoo. ``mode`` selects what the single ``read`` returns:

    * ``metadata`` — identity only, no bytes: ``id``, ``name``, ``mimetype``,
      ``file_size``, ``res_model``, ``res_id``, ``create_date``.
    * ``text`` — decode the binary payload and convert it to Markdown via
      MarkItDown (PDF / Office documents / CSV / HTML → Markdown). The decoded
      payload is capped at :data:`MAX_ATTACHMENT_BYTES`; a larger payload is
      truncated to the cap before conversion and the result carries
      ``truncated: True``. An unsupported or unconvertible format (or an empty
      payload) degrades to ``text=""`` plus an explanatory ``note`` — never a
      raised error.
    * ``raw`` — the base64 ``datas`` payload, refusing anything whose decoded
      size exceeds :data:`MAX_ATTACHMENT_BYTES` with a ``ValueError`` naming the
      size and the cap.

    Every result echoes the requested ``mode`` and carries the metadata fields.

    :param client: The Odoo API client.
    :type client: OdooClient
    :param attachment_id: The ``ir.attachment`` id to read.
    :type attachment_id: int
    :param mode: One of :data:`READ_ATTACHMENT_MODES`; defaults to ``"text"``.
    :type mode: str
    :raises ValueError: When ``mode`` is not a valid mode, or (in ``raw`` mode)
        the payload exceeds the size cap.
    :raises OdooMissingRecordError: When no attachment with ``attachment_id``
        exists or it is not accessible.
    :return: A metadata dict, extended per ``mode`` with ``text``/``truncated``
        (``text``), ``datas`` (``raw``), or nothing further (``metadata``).
    :rtype: dict[str, Any]
    """
    if mode not in READ_ATTACHMENT_MODES:
        raise ValueError(
            f"Invalid mode {mode!r}: expected one of 'text', 'metadata', 'raw'."
        )

    fields = list(_READ_METADATA_FIELDS)
    if mode in ("text", "raw"):
        fields.append("datas")

    records = client.execute("ir.attachment", "read", [attachment_id], fields=fields)
    if not records:
        raise OdooMissingRecordError(
            f"ir.attachment {attachment_id} does not exist or is not accessible.",
            model="ir.attachment",
            method="read",
        )
    record = records[0]
    metadata = _attachment_metadata(record, mode)

    if mode == "metadata":
        return metadata
    if mode == "raw":
        return _raw_result(metadata, record, attachment_id)
    return _text_result(metadata, record)
