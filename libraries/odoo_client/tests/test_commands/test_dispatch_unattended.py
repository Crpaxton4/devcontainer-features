"""A dispatched (``ODOO_UNATTENDED``) run emits no dispatch agent events.

The job row measures a dispatched run, so the CLI ``cmd`` dispatcher and the MCP
tool wrapper write their ``agent`` event only for an interactive session.
"""

from unittest.mock import Mock, patch

import pytest

from odoo_client.commands.dispatch_telemetry import _is_dispatched_run
from tests.support import make_event_store


class TestAgentEmission:
    def test_unattended_detection(self, monkeypatch):
        monkeypatch.delenv("ODOO_UNATTENDED", raising=False)
        assert not _is_dispatched_run()
        monkeypatch.setenv("ODOO_UNATTENDED", "")
        assert not _is_dispatched_run()
        monkeypatch.setenv("ODOO_UNATTENDED", "0")  # the guard hook: non-empty is set
        assert _is_dispatched_run()
        monkeypatch.setenv("ODOO_UNATTENDED", "1")
        assert _is_dispatched_run()

    def test_cli_cmd_emits_for_interactive_and_not_for_dispatched(self, monkeypatch):
        pytest.importorskip("fastmcp")
        pytest.importorskip("markdown_it")
        from odoo_client.cli import __main__ as cli

        registry = Mock()
        with patch.object(cli, "_emit_tool_event") as emit:
            monkeypatch.delenv("ODOO_UNATTENDED", raising=False)
            cli._emit_cmd_event(registry, "x", {}, {})
            assert emit.call_count == 1
            monkeypatch.setenv("ODOO_UNATTENDED", "1")
            cli._emit_cmd_event(registry, "x", {}, {})
            assert emit.call_count == 1

    def test_mcp_wrapper_emits_for_interactive_and_not_for_dispatched(
        self, monkeypatch, tmp_path
    ):
        pytest.importorskip("fastmcp")
        from odoo_client.commands import Registry
        from odoo_client.mcp.server import _event_emitting

        db = make_event_store(tmp_path)
        tool = _event_emitting(
            lambda task_id: {"task_id": task_id}, "t", Registry(Mock(), state_client=db)
        )
        monkeypatch.delenv("ODOO_UNATTENDED", raising=False)
        tool(task_id=5)
        assert len(db.get_events()) == 1
        monkeypatch.setenv("ODOO_UNATTENDED", "1")
        tool(task_id=5)
        assert len(db.get_events()) == 1
