"""Tests for the read-only ``get_mail_status`` MCP tool (issue #389).

The helper is driven through a real :class:`OdooClient` wrapping a recording
fake executor so the exact ``mail.message`` / ``mail.mail`` search domains,
fields, and the ``fields_get`` capability probe issued to Odoo are asserted, and
the message-to-mail join, recipients summary, and per-state shaping are exercised
end-to-end offline. ``mail.mail`` is frequently admin-restricted, so the
access-denied path is pinned to its exact ``ValueError`` message. No live Odoo is
used.
"""

import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from odoo_sdk.client import OdooClient
from odoo_sdk.commands.builtin import BUILTIN_COMMANDS
from odoo_sdk.commands.builtin.get_mail_status import GetMailStatusCommand
from odoo_sdk.transport.errors import OdooAccessError
from odoo_sdk.transport.executor import OdooExecutor
from odoo_sdk.transport.errors import OdooError
from odoo_sdk.utilities.mail_status import (
    MAIL_ACCESS_DENIED_MESSAGE,
    _m2o_id,
    _recipient_names,
    _summarize_recipients,
    get_mail_status,
)

_MESSAGE_FIELDS = ["id", "date", "subject"]
_MAIL_BASE_FIELDS = [
    "id",
    "mail_message_id",
    "subject",
    "state",
    "email_to",
    "recipient_ids",
]


def _message(mid: int, subject: str = "Send & Print") -> dict:
    """Build a raw ``mail.message`` row."""
    return {"id": mid, "date": f"2026-07-15 10:0{mid % 10}:00", "subject": subject}


def _mail(mid: int, message_id: int, state: str, **overrides: Any) -> dict:
    """Build a raw ``mail.mail`` row linked to ``message_id``."""
    row = {
        "id": mid,
        "mail_message_id": [message_id, f"msg-{message_id}"],
        "subject": f"Subject {mid}",
        "state": state,
        "email_to": "recipient@example.com",
        "recipient_ids": [],
    }
    row.update(overrides)
    return row


class _RecordingExecutor(OdooExecutor):
    """Fake executor recording every call and dispatching canned data by model.

    Real ``OdooClient`` execution runs through this (including the system-wide
    ``forbid_unlink`` guard), and every issued call is captured in ``calls`` so
    the exact domains / fields / order can be asserted. ``deny_mail`` makes the
    ``mail.mail`` read raise :class:`OdooAccessError`, modelling an
    admin-restricted outgoing mail queue.
    """

    def __init__(
        self,
        *,
        messages: list[dict] | None = None,
        mail_fields: dict | None = None,
        mails: list[dict] | None = None,
        partners: list[dict] | None = None,
        deny_mail: bool = False,
    ) -> None:
        self._messages = messages if messages is not None else []
        self._mail_fields = (
            mail_fields
            if mail_fields is not None
            else {"failure_reason": {}, "failure_type": {}}
        )
        self._mails = mails if mails is not None else []
        self._partners = partners if partners is not None else []
        self._deny_mail = deny_mail
        self.calls: list[tuple[str, str, tuple[Any, ...], dict[str, Any]]] = []

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((model, method, args, kwargs))
        if (model, method) == ("mail.message", "search_read"):
            return self._messages
        if model == "mail.mail" and method == "fields_get":
            if self._deny_mail:
                raise OdooAccessError("You are not allowed to access 'Outgoing Mail'.")
            return self._mail_fields
        if (model, method) == ("mail.mail", "search_read"):
            if self._deny_mail:
                raise OdooAccessError("You are not allowed to access 'Outgoing Mail'.")
            return self._mails
        if (model, method) == ("res.partner", "read"):
            return self._partners
        raise AssertionError(f"unexpected call: {model}.{method}")


def _client(**kwargs: Any) -> tuple[OdooClient, _RecordingExecutor]:
    executor = _RecordingExecutor(**kwargs)
    return OdooClient(executor=executor), executor


class TestGetMailStatusQuery(unittest.TestCase):
    """The reads use the exact message / mail domains and the failure probe."""

    def test_message_search_domain_and_fields(self):
        client, executor = _client(
            messages=[_message(101)], mails=[_mail(1, 101, "sent")]
        )
        get_mail_status(client, "project.task", 42)
        model, method, args, kwargs = executor.calls[0]
        self.assertEqual((model, method), ("mail.message", "search_read"))
        self.assertEqual(args[0], [("model", "=", "project.task"), ("res_id", "=", 42)])
        self.assertEqual(kwargs["fields"], _MESSAGE_FIELDS)

    def test_probes_mail_failure_fields_then_reads(self):
        client, executor = _client(
            messages=[_message(101)], mails=[_mail(1, 101, "sent")]
        )
        get_mail_status(client, "project.task", 42)
        # message search, then the fields_get probe, then the mail search.
        self.assertEqual(executor.calls[1][:2], ("mail.mail", "fields_get"))
        self.assertEqual(executor.calls[1][2][0], ["failure_reason", "failure_type"])
        model, method, args, kwargs = executor.calls[2]
        self.assertEqual((model, method), ("mail.mail", "search_read"))
        self.assertEqual(args[0], [("mail_message_id", "in", [101])])
        self.assertEqual(
            kwargs["fields"],
            [*_MAIL_BASE_FIELDS, "failure_reason", "failure_type"],
        )

    def test_absent_failure_field_omitted_from_mail_read(self):
        # Only failure_reason exists on this deployment; failure_type is not
        # requested, so no "Invalid field" fault can arise.
        client, executor = _client(
            messages=[_message(101)],
            mail_fields={"failure_reason": {}},
            mails=[_mail(1, 101, "sent")],
        )
        get_mail_status(client, "project.task", 42)
        _, _, _, kwargs = executor.calls[2]
        self.assertEqual(kwargs["fields"], [*_MAIL_BASE_FIELDS, "failure_reason"])

    def test_no_messages_short_circuits(self):
        client, executor = _client(messages=[])
        self.assertEqual(get_mail_status(client, "project.task", 42), [])
        # Only the message search ran; no mail.mail calls were issued.
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(executor.calls[0][:2], ("mail.message", "search_read"))


class TestGetMailStatusStates(unittest.TestCase):
    """Sent, outgoing, and exception mails are each reported correctly."""

    def _three_state_client(self):
        messages = [_message(101), _message(102, subject="Fallback subject"), _message(103)]
        mails = [
            _mail(1, 101, "sent", email_to="alice@example.com"),
            _mail(2, 102, "outgoing", subject=False, email_to="bob@example.com", recipient_ids=[7]),
            _mail(
                3,
                103,
                "exception",
                email_to="carol@example.com",
                failure_reason="SMTP timeout",
                failure_type="mail_smtp",
            ),
        ]
        return _client(messages=messages, mails=mails, partners=[{"id": 7, "name": "Bob P"}])

    def test_all_three_states_returned(self):
        client, _ = self._three_state_client()
        result = get_mail_status(client, "project.task", 42)
        self.assertEqual([r["state"] for r in result], ["sent", "outgoing", "exception"])
        self.assertEqual([r["mail_id"] for r in result], [1, 2, 3])
        self.assertEqual([r["message_id"] for r in result], [101, 102, 103])

    def test_failure_fields_only_on_exception(self):
        client, _ = self._three_state_client()
        result = get_mail_status(client, "project.task", 42)
        # sent + outgoing carry no failure keys ...
        self.assertNotIn("failure_reason", result[0])
        self.assertNotIn("failure_type", result[0])
        self.assertNotIn("failure_reason", result[1])
        # ... the exception surfaces both.
        self.assertEqual(result[2]["failure_reason"], "SMTP timeout")
        self.assertEqual(result[2]["failure_type"], "mail_smtp")

    def test_subject_falls_back_to_message_subject(self):
        client, _ = self._three_state_client()
        result = get_mail_status(client, "project.task", 42)
        # mail 1 keeps its own subject; mail 2 (blank subject) inherits the message's.
        self.assertEqual(result[0]["subject"], "Subject 1")
        self.assertEqual(result[1]["subject"], "Fallback subject")

    def test_date_comes_from_linked_message(self):
        client, _ = self._three_state_client()
        result = get_mail_status(client, "project.task", 42)
        self.assertEqual(result[0]["date"], "2026-07-15 10:01:00")

    def test_recipients_summarize_email_to_and_partner_names(self):
        client, executor = self._three_state_client()
        result = get_mail_status(client, "project.task", 42)
        self.assertEqual(result[0]["recipients"], "alice@example.com")
        # mail 2 combines its email_to with the resolved recipient partner name.
        self.assertEqual(result[1]["recipients"], "bob@example.com, Bob P")
        # res.partner was read exactly once (batched across all mails).
        partner_reads = [c for c in executor.calls if c[:2] == ("res.partner", "read")]
        self.assertEqual(len(partner_reads), 1)
        self.assertEqual(partner_reads[0][2][0], [7])


class TestGetMailStatusAccessDenied(unittest.TestCase):
    """A denied ``mail.mail`` read surfaces one stable, actionable message."""

    def test_denied_read_raises_exact_message(self):
        client, _ = _client(messages=[_message(101)], deny_mail=True)
        with self.assertRaises(ValueError) as ctx:
            get_mail_status(client, "project.task", 42)
        self.assertEqual(str(ctx.exception), MAIL_ACCESS_DENIED_MESSAGE)
        self.assertEqual(
            MAIL_ACCESS_DENIED_MESSAGE,
            "Access denied reading mail.mail (the outgoing mail queue). This "
            "model is commonly restricted to administrators; ask an Odoo admin "
            "to grant read access on mail.mail to verify outgoing-mail delivery "
            "status.",
        )


class TestSummarizeRecipients(unittest.TestCase):
    """The recipients summary de-dupes, drops blanks, and joins with commas."""

    def test_partner_read_failure_degrades_to_email_to(self):
        # When res.partner cannot be read, names are unknown; the summary keeps
        # the raw email_to addresses rather than aborting.
        row = {"email_to": "x@example.com", "recipient_ids": [9]}
        self.assertEqual(_summarize_recipients(row, {}), "x@example.com")

    def test_dedupes_and_drops_blanks(self):
        row = {"email_to": "a@x.com, , a@x.com", "recipient_ids": [5]}
        self.assertEqual(_summarize_recipients(row, {5: "a@x.com"}), "a@x.com")

    def test_empty_when_no_recipients(self):
        self.assertEqual(_summarize_recipients({}, {}), "")


class TestRecipientNames(unittest.TestCase):
    """Partner-name resolution is batched and degrades on a denied read."""

    def test_scalar_mail_message_id_is_returned_unchanged(self):
        # Some serializations return a bare id rather than an [id, name] pair.
        self.assertEqual(_m2o_id(55), 55)

    def test_partner_read_error_returns_empty_map(self):
        class _BoomExecutor(OdooExecutor):
            def execute(self, model, method, *args, **kwargs):
                raise OdooError("res.partner read failed")

        client = OdooClient(executor=_BoomExecutor())
        rows = [{"recipient_ids": [7]}]
        self.assertEqual(_recipient_names(client, rows), {})


class TestGetMailStatusCommand(unittest.TestCase):
    """The built-in command registers and delegates to the helper."""

    def test_registered_under_name(self):
        self.assertIn("get_mail_status", BUILTIN_COMMANDS)
        self.assertIs(BUILTIN_COMMANDS["get_mail_status"], GetMailStatusCommand)

    def test_execute_delegates_args(self):
        client = MagicMock()
        target = "odoo_sdk.commands.builtin.get_mail_status.get_mail_status"
        with patch(target, return_value=["shaped"]) as helper:
            result = GetMailStatusCommand(client).execute("project.task", 7)
        self.assertEqual(result, ["shaped"])
        helper.assert_called_once_with(client, "project.task", 7)

    def test_execute_is_read_only(self):
        client, executor = _client(
            messages=[_message(101)], mails=[_mail(1, 101, "sent")]
        )
        GetMailStatusCommand(client).execute("project.task", 42)
        methods = {method for _, method, _, _ in executor.calls}
        # Only read-only methods were issued — no create/write/unlink.
        self.assertTrue(methods <= {"search_read", "fields_get", "read"})


class TestGetMailStatusToolListing(unittest.TestCase):
    """The tool is registered on the MCP server's tool surface (issue #389)."""

    def test_tool_appears_in_server_listing(self):
        from odoo_sdk.commands import Registry
        from odoo_sdk.commands.builtin import register_builtins
        from odoo_sdk.mcp.server import OdooMCPServer
        from odoo_sdk.mcp.tools import build_explicit_tools

        registry = register_builtins(Registry(MagicMock()))
        tools = build_explicit_tools(registry)
        self.assertIn("get_mail_status", tools)

        added: list[Any] = []
        mock_mcp = MagicMock()
        mock_mcp.add_tool.side_effect = added.append
        with patch("odoo_sdk.mcp.server.FastMCP", return_value=mock_mcp):
            OdooMCPServer(registry, explicit_tools=tools)
        self.assertIn("get_mail_status", {tool.name for tool in added})


class TestGetMailStatusToonEncoding(unittest.TestCase):
    """The list-of-dicts result encodes cleanly under the TOON output flag."""

    def test_result_toon_encodes(self):
        from odoo_sdk.mcp.server import TOON_OUTPUT_ENV, _to_toon

        client, _ = _client(
            messages=[_message(103)],
            mails=[_mail(3, 103, "exception", failure_reason="SMTP timeout")],
        )
        result = get_mail_status(client, "project.task", 42)
        with patch.dict("os.environ", {TOON_OUTPUT_ENV: "1"}):
            out = _to_toon(result)
        self.assertIsInstance(out, str)
        self.assertIn("exception", out)
        self.assertIn("SMTP timeout", out)


if __name__ == "__main__":
    unittest.main()
