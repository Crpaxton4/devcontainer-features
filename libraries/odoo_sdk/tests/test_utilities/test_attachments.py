"""Tests for the task-attachment helper (issue #191).

The helper lists a ``project.task``'s attachments from two sources — the task's
own ``ir.attachment`` records and its chatter (``mail.message``) attachments —
deduped by attachment id, with the raw ``datas`` bytes gated behind
``include_content``. A ``MagicMock`` client stands in for Odoo so each
``execute`` call and the assembled result shape are checked directly.
"""

import unittest
from unittest.mock import MagicMock

from odoo_sdk.utilities.attachments import get_task_attachments

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


if __name__ == "__main__":
    unittest.main()
