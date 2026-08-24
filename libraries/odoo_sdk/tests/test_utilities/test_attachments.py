"""Tests for the task-attachment helper (issue #191).

The helper lists a ``project.task``'s attachments from two sources — the task's
own ``ir.attachment`` records and its chatter (``mail.message``) attachments —
deduped by attachment id, with the raw ``datas`` bytes gated behind
``include_content``. A ``MagicMock`` client stands in for Odoo so each
``execute`` call and the assembled result shape are checked directly.
"""

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from odoo_sdk.utilities.attachments import (
    create_attachment,
    create_attachments,
    get_task_attachments,
)

_METADATA_FIELDS = ["name", "mimetype", "file_size", "create_date"]
_CONTENT_FIELDS = _METADATA_FIELDS + ["datas"]


def _attachment(attachment_id, name="file.png", **overrides):
    record = {
        "id": attachment_id,
        "name": name,
        "mimetype": "image/png",
        "file_size": 1234,
        "create_date": "2026-07-10 12:00:00",
    }
    record.update(overrides)
    return record


def _client_with(task_records, messages, message_records):
    """Build a MagicMock client whose ``execute`` routes by (model, method).

    ``task_records`` answers the direct-task ``ir.attachment`` search_read,
    ``messages`` answers the ``mail.message`` search_read, and
    ``message_records`` answers the follow-up ``ir.attachment`` read.
    """
    client = MagicMock()

    def _execute(model, method, *args, **kwargs):
        if (model, method) == ("ir.attachment", "search_read"):
            return task_records
        if (model, method) == ("mail.message", "search_read"):
            return messages
        if (model, method) == ("ir.attachment", "read"):
            return message_records
        raise AssertionError(f"unexpected call: {model}.{method}")

    client.execute.side_effect = _execute
    return client


class TestGetTaskAttachments(unittest.TestCase):
    def test_direct_task_search_read_call(self):
        client = _client_with([], [], [])
        get_task_attachments(client, task_id=42)
        client.execute.assert_any_call(
            "ir.attachment",
            "search_read",
            [("res_model", "=", "project.task"), ("res_id", "=", 42)],
            fields=_METADATA_FIELDS,
        )

    def test_message_search_read_call(self):
        client = _client_with([], [], [])
        get_task_attachments(client, task_id=42)
        client.execute.assert_any_call(
            "mail.message",
            "search_read",
            [("model", "=", "project.task"), ("res_id", "=", 42)],
            fields=["id", "attachment_ids"],
        )

    def test_returns_task_attachments_with_source(self):
        client = _client_with([_attachment(1)], [], [])
        result = get_task_attachments(client, task_id=1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], 1)
        self.assertEqual(result[0]["name"], "file.png")
        self.assertEqual(result[0]["mimetype"], "image/png")
        self.assertEqual(result[0]["file_size"], 1234)
        self.assertEqual(result[0]["create_date"], "2026-07-10 12:00:00")
        self.assertEqual(result[0]["source"], "task")

    def test_returns_message_attachments_with_source(self):
        client = _client_with(
            [],
            [{"id": 10, "attachment_ids": [5]}],
            [_attachment(5, name="chatter.pdf", mimetype="application/pdf")],
        )
        result = get_task_attachments(client, task_id=1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], 5)
        self.assertEqual(result[0]["source"], "message")
        # The collected chatter ids are read as a positional id list + kw fields.
        client.execute.assert_any_call(
            "ir.attachment", "read", [5], fields=_METADATA_FIELDS
        )

    def test_combines_both_sources(self):
        client = _client_with(
            [_attachment(1)],
            [{"id": 10, "attachment_ids": [2]}],
            [_attachment(2, name="chatter.pdf")],
        )
        result = get_task_attachments(client, task_id=1)
        by_id = {r["id"]: r["source"] for r in result}
        self.assertEqual(by_id, {1: "task", 2: "message"})

    def test_dedupes_shared_attachment_keeping_task_source(self):
        # Attachment 1 is linked to the task AND surfaced via chatter.
        client = _client_with(
            [_attachment(1)],
            [{"id": 10, "attachment_ids": [1]}],
            [_attachment(1)],
        )
        result = get_task_attachments(client, task_id=1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source"], "task")

    def test_dedupes_within_chatter(self):
        # Same attachment referenced from two messages.
        client = _client_with(
            [],
            [{"id": 10, "attachment_ids": [7]}, {"id": 11, "attachment_ids": [7]}],
            [_attachment(7)],
        )
        result = get_task_attachments(client, task_id=1)
        self.assertEqual([r["id"] for r in result], [7])

    def test_no_read_when_no_message_attachments(self):
        client = _client_with([], [{"id": 10, "attachment_ids": []}], [])
        get_task_attachments(client, task_id=1)
        # No follow-up ``read`` call is issued for an empty id set.
        for c in client.execute.call_args_list:
            self.assertNotEqual(c.args[:2], ("ir.attachment", "read"))

    def test_include_content_false_omits_datas(self):
        client = _client_with([_attachment(1)], [], [])
        result = get_task_attachments(client, task_id=1, include_content=False)
        self.assertNotIn("datas", result[0])
        client.execute.assert_any_call(
            "ir.attachment",
            "search_read",
            [("res_model", "=", "project.task"), ("res_id", "=", 1)],
            fields=_METADATA_FIELDS,
        )

    def test_include_content_true_includes_datas_and_fields(self):
        client = _client_with(
            [_attachment(1, datas="QUJD")],
            [{"id": 10, "attachment_ids": [2]}],
            [_attachment(2, datas="REVG")],
        )
        result = get_task_attachments(client, task_id=1, include_content=True)
        by_id = {r["id"]: r for r in result}
        self.assertEqual(by_id[1]["datas"], "QUJD")
        self.assertEqual(by_id[2]["datas"], "REVG")
        # ``datas`` is requested from both the search_read and the read.
        client.execute.assert_any_call(
            "ir.attachment",
            "search_read",
            [("res_model", "=", "project.task"), ("res_id", "=", 1)],
            fields=_CONTENT_FIELDS,
        )
        client.execute.assert_any_call(
            "ir.attachment", "read", [2], fields=_CONTENT_FIELDS
        )

    def test_returns_empty_list_when_no_attachments(self):
        client = _client_with([], [], [])
        self.assertEqual(get_task_attachments(client, task_id=99), [])


# --------------------------------------------------------------------------- #
# create_attachment / create_attachments (#604): the upload path.
# --------------------------------------------------------------------------- #

_B64_HELLO = base64.b64encode(b"hello world").decode("ascii")


def _create_client(create_result=101):
    """MagicMock client that only answers ``ir.attachment`` ``create`` calls."""
    client = MagicMock()

    def _execute(model, method, *args, **kwargs):
        if (model, method) == ("ir.attachment", "create"):
            return create_result
        raise AssertionError(f"unexpected call: {model}.{method}")

    client.execute.side_effect = _execute
    return client


class TestCreateAttachment(unittest.TestCase):
    def _tmp_file(self, name="report.csv", payload=b"a,b\n1,2\n") -> str:
        tmp_dir = tempfile.mkdtemp()
        path = Path(tmp_dir) / name
        path.write_bytes(payload)
        return str(path)

    def test_content_spec_creates_ir_attachment(self):
        client = _create_client(create_result=42)
        result = create_attachment(
            client,
            content=_B64_HELLO,
            name="findings.md",
            mimetype="text/markdown",
        )
        client.execute.assert_called_once_with(
            "ir.attachment",
            "create",
            {
                "name": "findings.md",
                "datas": _B64_HELLO,
                "mimetype": "text/markdown",
            },
        )
        self.assertEqual(result, 42)

    def test_path_spec_reads_and_base64_encodes_file(self):
        path = self._tmp_file(payload=b"hello world")
        client = _create_client()
        create_attachment(client, path=path)
        values = client.execute.call_args.args[2]
        self.assertEqual(values["name"], "report.csv")
        self.assertEqual(base64.b64decode(values["datas"]), b"hello world")

    def test_mimetype_guessed_from_filename(self):
        client = _create_client()
        create_attachment(client, content=_B64_HELLO, name="notes.txt")
        values = client.execute.call_args.args[2]
        self.assertEqual(values["mimetype"], "text/plain")

    def test_mimetype_falls_back_to_octet_stream(self):
        client = _create_client()
        create_attachment(client, content=_B64_HELLO, name="blob.unknownext")
        values = client.execute.call_args.args[2]
        self.assertEqual(values["mimetype"], "application/octet-stream")

    def test_explicit_name_overrides_path_basename(self):
        path = self._tmp_file()
        client = _create_client()
        create_attachment(client, path=path, name="renamed.csv")
        values = client.execute.call_args.args[2]
        self.assertEqual(values["name"], "renamed.csv")

    def test_res_model_and_res_id_link_the_record(self):
        client = _create_client()
        create_attachment(
            client,
            content=_B64_HELLO,
            name="a.txt",
            res_model="project.task",
            res_id=7,
        )
        values = client.execute.call_args.args[2]
        self.assertEqual(values["res_model"], "project.task")
        self.assertEqual(values["res_id"], 7)

    def test_unlinked_create_omits_res_fields(self):
        client = _create_client()
        create_attachment(client, content=_B64_HELLO, name="a.txt")
        values = client.execute.call_args.args[2]
        self.assertNotIn("res_model", values)
        self.assertNotIn("res_id", values)

    def test_list_wrapped_create_result_is_unwrapped_to_int(self):
        # Odoo answers a batch-shaped create with ``[id]``; callers must always
        # get a scalar int either way.
        client = _create_client(create_result=[55])
        self.assertEqual(
            create_attachment(client, content=_B64_HELLO, name="a.txt"), 55
        )

    def test_rejects_both_path_and_content(self):
        with self.assertRaises(ValueError) as ctx:
            create_attachment(
                _create_client(), path="/tmp/x", content=_B64_HELLO, name="a"
            )
        self.assertIn("exactly one", str(ctx.exception))

    def test_rejects_neither_path_nor_content(self):
        with self.assertRaises(ValueError):
            create_attachment(_create_client(), name="a.txt")

    def test_rejects_content_without_name(self):
        with self.assertRaises(ValueError) as ctx:
            create_attachment(_create_client(), content=_B64_HELLO)
        self.assertIn("name", str(ctx.exception))

    def test_rejects_invalid_base64_content(self):
        with self.assertRaises(ValueError) as ctx:
            create_attachment(
                _create_client(), content="not base64!!", name="a.txt"
            )
        self.assertIn("base64", str(ctx.exception))

    def test_rejects_unreadable_path(self):
        with self.assertRaises(ValueError) as ctx:
            create_attachment(_create_client(), path="/no/such/file.bin")
        self.assertIn("not a readable file", str(ctx.exception))

    def test_validation_failure_issues_no_rpc_call(self):
        client = _create_client()
        with self.assertRaises(ValueError):
            create_attachment(client, content="not base64!!", name="a.txt")
        client.execute.assert_not_called()


class TestCreateAttachments(unittest.TestCase):
    def test_creates_one_record_per_spec_in_order(self):
        ids = iter([11, 12])
        client = MagicMock()

        def _execute(model, method, *args, **kwargs):
            self.assertEqual((model, method), ("ir.attachment", "create"))
            return next(ids)

        client.execute.side_effect = _execute
        result = create_attachments(
            client,
            [
                {"content": _B64_HELLO, "name": "one.txt"},
                {"content": _B64_HELLO, "name": "two.txt"},
            ],
            res_model="project.task",
            res_id=9,
        )
        self.assertEqual(result, [11, 12])
        self.assertEqual(client.execute.call_count, 2)
        names = [call.args[2]["name"] for call in client.execute.call_args_list]
        self.assertEqual(names, ["one.txt", "two.txt"])
        for call in client.execute.call_args_list:
            self.assertEqual(call.args[2]["res_model"], "project.task")
            self.assertEqual(call.args[2]["res_id"], 9)

    def test_rejects_empty_file_list(self):
        with self.assertRaises(ValueError) as ctx:
            create_attachments(_create_client(), [])
        self.assertIn("at least one", str(ctx.exception))

    def test_rejects_non_dict_spec(self):
        with self.assertRaises(ValueError) as ctx:
            create_attachments(_create_client(), ["/tmp/file.txt"])
        self.assertIn("#1", str(ctx.exception))

    def test_rejects_unknown_spec_keys(self):
        with self.assertRaises(ValueError) as ctx:
            create_attachments(
                _create_client(),
                [{"content": _B64_HELLO, "filename": "typo.txt"}],
            )
        self.assertIn("filename", str(ctx.exception))

    def test_one_bad_spec_creates_nothing(self):
        # Every spec is validated before the first record is created, so a
        # malformed second spec never leaves a partial batch behind.
        client = _create_client()
        with self.assertRaises(ValueError):
            create_attachments(
                client,
                [
                    {"content": _B64_HELLO, "name": "good.txt"},
                    {"content": "not base64!!", "name": "bad.txt"},
                ],
            )
        client.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
