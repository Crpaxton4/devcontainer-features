"""Odoo API helpers for a task's attachments (list, single read, create).

The single owner of the task-attachment read path. The full two-source /
dedupe / opt-in-``datas`` story lives on :func:`get_task_attachments`;
:func:`read_attachment` reads one already-stored ``ir.attachment`` by id.

The write path (#604) is :func:`create_attachment` / :func:`create_attachments`:
upload one or more files (local path or base64 content) as ``ir.attachment``
records so chatter posts can link them via ``message_post``'s
``attachment_ids``. The read helpers above stay strictly read-only.
"""

import base64
import io
import mimetypes
import os
from typing import Any, Optional

from markitdown import MarkItDown

from odoo_sdk import OdooMissingRecordError
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
    each result. Task-linked attachments are returned first.
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
    Raises :class:`OdooMissingRecordError` when no attachment ``attachment_id``
    exists, and :class:`ValueError` for an invalid ``mode`` or an oversized
    ``raw`` payload.
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


# --------------------------------------------------------------------------- #
# create_attachment(s): upload files as ir.attachment records (#604).
# --------------------------------------------------------------------------- #

#: The keys a file spec dict handed to :func:`create_attachments` may carry.
#: Anything else (e.g. a ``filename`` typo for ``name``) is rejected up front so
#: a mis-keyed spec cannot silently drop its payload or metadata.
_FILE_SPEC_KEYS = frozenset({"path", "content", "name", "mimetype"})

#: Fallback mimetype when neither the caller nor ``mimetypes.guess_type`` can
#: classify the file. Odoo would sniff one server-side, but sending an explicit
#: value keeps the created record deterministic across transports.
_DEFAULT_MIMETYPE = "application/octet-stream"


def _scalar_create_id(result: Any) -> int:
    """Unwrap a possibly list-wrapped Odoo ``create`` result to a scalar int.

    Odoo answers a batch (list-of-dicts) ``create`` with ``[id]`` and a single
    (dict) call with a scalar ``id``; unwrapping here guarantees callers always
    get a plain int (same defence as ``odoo_sdk.billing.timesheet._scalar_id``).
    """
    if isinstance(result, (list, tuple)):
        return int(result[0])
    return int(result)


def _resolve_payload(
    path: Optional[str], content: Optional[str], name: Optional[str]
) -> tuple[str, str]:
    """Resolve a file spec to its ``(base64 payload, filename)`` pair.

    Pure validation/IO helper shared by the create path: exactly one of
    ``path`` / ``content`` must be given. A ``path`` is read from disk and
    base64-encoded (``name`` defaults to its basename); a ``content`` payload
    must already be valid base64 and requires an explicit ``name``. Every
    failure raises :class:`ValueError` with an actionable message.
    """
    if (path is None) == (content is None):
        raise ValueError(
            "Provide exactly one of 'path' (a readable file) or 'content' "
            "(base64-encoded bytes) per attachment."
        )
    if path is not None:
        if not os.path.isfile(path):
            raise ValueError(f"Attachment path {path!r} is not a readable file.")
        with open(path, "rb") as handle:
            payload = base64.b64encode(handle.read()).decode("ascii")
        return payload, name or os.path.basename(path)
    if not name:
        raise ValueError(
            "Attachment 'name' (the filename) is required when passing "
            "base64 'content'."
        )
    try:
        base64.b64decode(content, validate=True)
    except Exception as exc:
        raise ValueError(
            f"Attachment 'content' for {name!r} is not valid base64: {exc}"
        ) from exc
    return content, name  # type: ignore[return-value]  # content is not None here


def _attachment_values(
    path: Optional[str],
    content: Optional[str],
    name: Optional[str],
    mimetype: Optional[str],
    res_model: Optional[str],
    res_id: Optional[int],
) -> dict[str, Any]:
    """Build the validated ``ir.attachment`` create values for one file."""
    payload, filename = _resolve_payload(path, content, name)
    values: dict[str, Any] = {
        "name": filename,
        "datas": payload,
        "mimetype": mimetype
        or mimetypes.guess_type(filename)[0]
        or _DEFAULT_MIMETYPE,
    }
    if res_model is not None:
        values["res_model"] = res_model
    if res_id is not None:
        values["res_id"] = res_id
    return values


def create_attachment(
    client: OdooClient,
    *,
    path: Optional[str] = None,
    content: Optional[str] = None,
    name: Optional[str] = None,
    mimetype: Optional[str] = None,
    res_model: Optional[str] = None,
    res_id: Optional[int] = None,
) -> int:
    """Create one ``ir.attachment`` record and return its id (#604).

    The file arrives either as ``path`` (a readable local file, read and
    base64-encoded here; ``name`` defaults to the basename) or as ``content``
    (already-base64 bytes, requiring an explicit ``name``) — exactly one of the
    two. ``mimetype`` defaults to a guess from the filename, falling back to
    ``application/octet-stream``. ``res_model`` / ``res_id`` optionally link the
    attachment to a record (e.g. ``project.task``) so it shows on that record
    and can be handed to ``message_post`` via ``attachment_ids``.

    Raises :class:`ValueError` for a missing/ambiguous payload, an unreadable
    path, invalid base64 content, or a content payload without a name.
    """
    values = _attachment_values(path, content, name, mimetype, res_model, res_id)
    return _scalar_create_id(client.execute("ir.attachment", "create", values))


def create_attachments(
    client: OdooClient,
    files: list[dict[str, Any]],
    *,
    res_model: Optional[str] = None,
    res_id: Optional[int] = None,
) -> list[int]:
    """Create one ``ir.attachment`` per file spec, returning the ids in order.

    Each spec is a dict with ``path`` *or* ``content`` (+ ``name``), plus an
    optional ``mimetype`` — the same contract as :func:`create_attachment`,
    which documents the per-file rules. All specs are validated (payloads
    resolved) *before* the first record is created, so one malformed spec never
    leaves a partial batch behind. An empty list and unknown spec keys raise
    :class:`ValueError`.
    """
    if not files:
        raise ValueError("Provide at least one attachment file spec.")
    for index, spec in enumerate(files):
        if not isinstance(spec, dict):
            raise ValueError(
                f"Attachment spec #{index + 1} must be a dict with 'path' or "
                f"'content' + 'name', got {type(spec).__name__}."
            )
        unknown = set(spec) - _FILE_SPEC_KEYS
        if unknown:
            raise ValueError(
                f"Attachment spec #{index + 1} has unknown key(s) "
                f"{sorted(unknown)}; allowed keys are "
                f"{sorted(_FILE_SPEC_KEYS)}."
            )
    all_values = [
        _attachment_values(
            spec.get("path"),
            spec.get("content"),
            spec.get("name"),
            spec.get("mimetype"),
            res_model,
            res_id,
        )
        for spec in files
    ]
    return [
        _scalar_create_id(client.execute("ir.attachment", "create", values))
        for values in all_values
    ]
