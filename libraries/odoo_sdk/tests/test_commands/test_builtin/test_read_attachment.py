"""Tests for the read-only ``read_attachment`` MCP tool (issue #247).

The helper reads one already-stored ``ir.attachment`` in one of three modes
(``metadata`` / ``text`` / ``raw``). It is driven through a real
:class:`OdooClient` wrapping a recording fake executor so the exact
``read`` call issued to Odoo is asserted, and the markitdown binary-stream
conversion is exercised end-to-end in-process (including a real hand-rolled
PDF). No live Odoo is used.
"""

import base64
import unittest
from typing import Any, Optional
from unittest.mock import MagicMock, patch

from odoo_sdk.client import OdooClient
from odoo_sdk.commands.builtin import BUILTIN_COMMANDS
from odoo_sdk.commands.builtin.read_attachment import ReadAttachmentCommand
from odoo_sdk.transport.errors import OdooMissingRecordError
from odoo_sdk.transport.executor import OdooExecutor
from odoo_sdk.utilities import attachments
from odoo_sdk.utilities.attachments import (
    MAX_ATTACHMENT_BYTES,
    _file_extension,
    read_attachment,
)

_METADATA_FIELDS = [
    "name",
    "mimetype",
    "file_size",
    "res_model",
    "res_id",
    "create_date",
]
_CONTENT_FIELDS = _METADATA_FIELDS + ["datas"]


def _b64(data: bytes) -> str:
    """Base64-encode bytes the way Odoo stores an attachment's ``datas``."""
    return base64.b64encode(data).decode("ascii")


def _attach(
    attachment_id: int = 7, datas: Optional[str] = None, **overrides: Any
) -> dict:
    """Build a raw ``ir.attachment`` record with sensible defaults."""
    record = {
        "id": attachment_id,
        "name": "report.csv",
        "mimetype": "text/csv",
        "file_size": 12,
        "res_model": "project.task",
        "res_id": 42,
        "create_date": "2026-07-10 12:00:00",
    }
    record.update(overrides)
    if datas is not None:
        record["datas"] = datas
    return record


class _RecordingExecutor(OdooExecutor):
    """Fake executor recording every call and returning a canned row list."""

    def __init__(self, rows: Optional[list[dict]] = None) -> None:
        self._rows = rows if rows is not None else []
        self.calls: list[tuple[str, str, tuple[Any, ...], dict[str, Any]]] = []

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((model, method, args, kwargs))
        return self._rows


def _client(rows: Optional[list[dict]] = None) -> tuple[OdooClient, _RecordingExecutor]:
    executor = _RecordingExecutor(rows)
    return OdooClient(executor=executor), executor


def _tiny_pdf(text: bytes = b"Hello PDF World") -> bytes:
    """Assemble a minimal, valid single-page PDF with a correct xref table.

    Built by hand (no reportlab) so the bonus end-to-end test proves the
    markitdown PDF path really runs in-process on a genuine PDF.
    """
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    ]
    stream = b"BT /F1 24 Tf 72 700 Td (" + text + b") Tj ET"
    objs.append(
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream"
    )
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += str(index).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += ("%010d 00000 n \n" % offset).encode()
    out += (
        b"trailer\n<< /Size " + str(len(objs) + 1).encode() + b" /Root 1 0 R >>\n"
        b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF"
    )
    return bytes(out)


class TestReadAttachmentCall(unittest.TestCase):
    """The single ``read`` call targets the right model/fields per mode."""

    def _call(self, executor: _RecordingExecutor):
        self.assertEqual(len(executor.calls), 1)
        return executor.calls[0]

    def test_metadata_mode_reads_only_metadata_fields(self):
        client, executor = _client([_attach()])
        read_attachment(client, 7, mode="metadata")
        model, method, args, kwargs = self._call(executor)
        self.assertEqual((model, method), ("ir.attachment", "read"))
        self.assertEqual(args[0], [7])
        self.assertEqual(kwargs["fields"], _METADATA_FIELDS)

    def test_text_mode_reads_datas_field(self):
        client, executor = _client([_attach(datas=_b64(b"a,b\n1,2\n"))])
        read_attachment(client, 7, mode="text")
        self.assertEqual(self._call(executor)[3]["fields"], _CONTENT_FIELDS)

    def test_raw_mode_reads_datas_field(self):
        client, executor = _client([_attach(datas=_b64(b"bytes"))])
        read_attachment(client, 7, mode="raw")
        self.assertEqual(self._call(executor)[3]["fields"], _CONTENT_FIELDS)


class TestReadAttachmentMetadataMode(unittest.TestCase):
    def test_returns_flat_metadata_and_echoes_mode(self):
        client, _ = _client([_attach()])
        result = read_attachment(client, 7, mode="metadata")
        self.assertEqual(
            result,
            {
                "id": 7,
                "name": "report.csv",
                "mimetype": "text/csv",
                "file_size": 12,
                "res_model": "project.task",
                "res_id": 42,
                "create_date": "2026-07-10 12:00:00",
                "mode": "metadata",
            },
        )
        # No payload keys leak into metadata mode.
        self.assertNotIn("text", result)
        self.assertNotIn("datas", result)

    def test_unlinked_attachment_normalizes_res_fields_to_none(self):
        client, _ = _client([_attach(res_model=False, res_id=False)])
        result = read_attachment(client, 7, mode="metadata")
        self.assertIsNone(result["res_model"])
        self.assertIsNone(result["res_id"])


class TestReadAttachmentTextMode(unittest.TestCase):
    def test_converts_payload_to_markdown(self):
        # A real CSV payload flows through markitdown's binary path to Markdown.
        client, _ = _client([_attach(datas=_b64(b"a,b,c\n1,2,3\n"))])
        result = read_attachment(client, 7, mode="text")
        self.assertEqual(result["mode"], "text")
        self.assertFalse(result["truncated"])
        self.assertIn("| a | b | c |", result["text"])
        self.assertIn("| 1 | 2 | 3 |", result["text"])
        self.assertNotIn("note", result)

    def test_oversized_payload_is_truncated_and_flagged(self):
        payload = b"abcdefghij"  # 10 decoded bytes
        client, _ = _client(
            [_attach(name="data.txt", mimetype="text/plain", datas=_b64(payload))]
        )
        with patch.object(attachments, "MAX_ATTACHMENT_BYTES", 4):
            result = read_attachment(client, 7, mode="text")
        self.assertTrue(result["truncated"])
        # Only the first 4 decoded bytes reached the converter.
        self.assertEqual(result["text"], "abcd")

    def test_converts_without_extension_hint(self):
        # No filename extension and no mimetype: markitdown sniffs the stream.
        client, _ = _client(
            [_attach(name="noext", mimetype=False, datas=_b64(b"plain text body"))]
        )
        result = read_attachment(client, 7, mode="text")
        self.assertFalse(result["truncated"])
        self.assertIn("plain text body", result["text"])

    def test_empty_payload_degrades_with_note(self):
        client, _ = _client([_attach(datas=False)])
        result = read_attachment(client, 7, mode="text")
        self.assertEqual(result["text"], "")
        self.assertFalse(result["truncated"])
        self.assertIn("no stored binary payload", result["note"])

    def test_conversion_failure_degrades_with_note(self):
        client, _ = _client(
            [
                _attach(
                    name="broken.pdf", mimetype="application/pdf", datas=_b64(b"data")
                )
            ]
        )
        with patch.object(
            attachments._md_converter,
            "convert_stream",
            side_effect=RuntimeError("boom"),
        ):
            result = read_attachment(client, 7, mode="text")
        self.assertEqual(result["text"], "")
        self.assertFalse(result["truncated"])
        self.assertIn("Could not extract text", result["note"])
        self.assertIn("broken.pdf", result["note"])
        self.assertIn("boom", result["note"])

    def test_real_pdf_extracted_end_to_end(self):
        # Bonus: a genuine hand-rolled PDF proves the markitdown PDF path runs
        # in-process (requires the markitdown[pdf] backend).
        client, _ = _client(
            [
                _attach(
                    name="doc.pdf", mimetype="application/pdf", datas=_b64(_tiny_pdf())
                )
            ]
        )
        result = read_attachment(client, 7, mode="text")
        self.assertFalse(result["truncated"])
        self.assertNotIn("note", result)
        self.assertIn("Hello PDF World", result["text"])


class TestReadAttachmentRawMode(unittest.TestCase):
    def test_returns_base64_payload(self):
        datas = _b64(b"raw-bytes")
        client, _ = _client([_attach(datas=datas)])
        result = read_attachment(client, 7, mode="raw")
        self.assertEqual(result["mode"], "raw")
        self.assertEqual(result["datas"], datas)

    def test_empty_payload_returns_none_datas(self):
        client, _ = _client([_attach(datas=False)])
        result = read_attachment(client, 7, mode="raw")
        self.assertIsNone(result["datas"])

    def test_oversized_payload_refused_naming_size_and_cap(self):
        client, _ = _client([_attach(datas=_b64(b"abcdefghij"))])  # 10 decoded bytes
        with patch.object(attachments, "MAX_ATTACHMENT_BYTES", 4):
            with self.assertRaises(ValueError) as ctx:
                read_attachment(client, 7, mode="raw")
        message = str(ctx.exception)
        self.assertIn("10 bytes", message)
        self.assertIn("4-byte cap", message)


class TestReadAttachmentErrors(unittest.TestCase):
    def test_invalid_mode_raises_valueerror_exact_message(self):
        client, executor = _client([_attach()])
        with self.assertRaises(ValueError) as ctx:
            read_attachment(client, 7, mode="binary")
        self.assertEqual(
            str(ctx.exception),
            "Invalid mode 'binary': expected one of 'text', 'metadata', 'raw'.",
        )
        # The bad mode is rejected before any Odoo call is made.
        self.assertEqual(executor.calls, [])

    def test_missing_attachment_raises_missing_record_error(self):
        # A nonexistent read returns no rows through the fake executor; the
        # helper surfaces the taxonomy's typed missing-record error naming the id.
        client, _ = _client([])
        with self.assertRaises(OdooMissingRecordError) as ctx:
            read_attachment(client, 999, mode="metadata")
        self.assertIn("999", str(ctx.exception))


class TestFileExtension(unittest.TestCase):
    def test_prefers_filename_extension_case_preserved(self):
        self.assertEqual(_file_extension("Report.PDF", "text/plain"), ".PDF")

    def test_falls_back_to_mimetype_when_name_has_no_extension(self):
        self.assertEqual(_file_extension("noext", "application/pdf"), ".pdf")

    def test_returns_none_when_neither_yields_extension(self):
        self.assertIsNone(_file_extension("noext", None))
        self.assertIsNone(_file_extension(None, None))


class TestReadAttachmentCommand(unittest.TestCase):
    """The built-in command registers and delegates to the helper."""

    def test_registered_under_name(self):
        self.assertIn("read_attachment", BUILTIN_COMMANDS)
        self.assertIs(BUILTIN_COMMANDS["read_attachment"], ReadAttachmentCommand)

    def test_execute_delegates_to_helper(self):
        client = MagicMock()
        target = "odoo_sdk.commands.builtin.read_attachment.read_attachment"
        with patch(target, return_value={"ok": True}) as helper:
            result = ReadAttachmentCommand(client).execute(7, mode="raw")
        self.assertEqual(result, {"ok": True})
        helper.assert_called_once_with(client, 7, mode="raw")

    def test_execute_defaults_to_text_mode(self):
        client, executor = _client([_attach(datas=_b64(b"a,b\n1,2\n"))])
        ReadAttachmentCommand(client).execute(7)
        # A single read-only ``read`` call, no writes of any kind.
        self.assertEqual(len(executor.calls), 1)
        model, method, _, kwargs = executor.calls[0]
        self.assertEqual((model, method), ("ir.attachment", "read"))
        self.assertIn("datas", kwargs["fields"])


class TestReadAttachmentToonEncoding(unittest.TestCase):
    """The result dict encodes cleanly under the TOON output flag."""

    def test_result_toon_encodes(self):
        from odoo_sdk.mcp.server import TOON_OUTPUT_ENV, _to_toon

        client, _ = _client([_attach(datas=_b64(b"a,b\n1,2\n"))])
        result = read_attachment(client, 7, mode="text")
        with patch.dict("os.environ", {TOON_OUTPUT_ENV: "1"}):
            out = _to_toon(result)
        self.assertIsInstance(out, str)
        self.assertIn("report.csv", out)


class TestReadAttachmentCap(unittest.TestCase):
    def test_default_cap_is_documented_ten_mib(self):
        self.assertEqual(MAX_ATTACHMENT_BYTES, 10 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
