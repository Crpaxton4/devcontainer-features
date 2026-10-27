"""Dispatch telemetry: error vocabulary, event emission and the unattended rule.

A dispatched (``ODOO_UNATTENDED``) run is measured by the job row, so the CLI
``cmd`` dispatcher and the MCP tool wrapper write their ``agent`` event only for
an interactive session.
"""

import subprocess
from unittest.mock import patch

import pytest

from odoo_client.errors import OdooError
from odoo_dev_core.telemetry import (
    ATTRIBUTING_TOOLS,
    BOUNDARY_ERRORS,
    READ_TASK_IDS_PAYLOAD_KEY,
    emit_tool_event,
    error_payload,
    is_dispatched_run,
)
from .fakes import service
from .support import make_event_store


class TestErrorBoundary:
    def test_boundary_errors_cover_odoo_git_and_input_faults(self):
        assert OdooError in BOUNDARY_ERRORS
        assert subprocess.CalledProcessError in BOUNDARY_ERRORS
        assert ValueError in BOUNDARY_ERRORS
        assert KeyError not in BOUNDARY_ERRORS

    def test_error_payload_names_the_concrete_class(self):
        assert error_payload(ValueError("bad")) == {
            "error": {"type": "ValueError", "message": "bad"}
        }


class TestEmitToolEvent:
    def _emit(self, name, arguments, result=None, **kwargs):
        db = make_event_store()
        with patch(
            "odoo_dev_core.builtins.log_event.current_branch_label",
            return_value="",
        ):
            emit_tool_event(service(events=db), name, arguments, result, **kwargs)
        return db.get_events()

    def test_mutating_tool_hints_its_task_id(self):
        (event,) = self._emit(
            "task_note", {"task_id": 7, "note": "x"}, {"message_id": 3}
        )
        assert "task_note" in ATTRIBUTING_TOOLS
        assert event.task_ids == ["7"]
        assert event.source == "agent"
        assert event.payload["args"] == ["note", "task_id"]
        assert event.payload["message_id"] == 3

    def test_read_tool_records_evidence_not_attribution(self):
        (event,) = self._emit("get_task", {"task_id": 7})
        assert event.task_ids == []
        assert event.payload[READ_TASK_IDS_PAYLOAD_KEY] == ["7"]

    def test_via_marker_is_recorded_when_given(self):
        (event,) = self._emit("get_uid", {}, via="cli")
        assert event.payload["via"] == "cli"


class TestUnattendedRule:
    def test_unattended_detection(self):
        assert not is_dispatched_run({})
        assert not is_dispatched_run({"ODOO_UNATTENDED": ""})
        # The guard hook rule: any non-empty value counts, including "0".
        assert is_dispatched_run({"ODOO_UNATTENDED": "0"})
        assert is_dispatched_run({"ODOO_UNATTENDED": "1"})

    def test_cli_cmd_emits_for_interactive_and_not_for_dispatched(self):
        pytest.importorskip("fastmcp")
        pytest.importorskip("markdown_it")
        from odoo_cli import __main__ as cli

        with patch.object(cli, "emit_tool_event") as emit:
            cli._emit_cmd_event(service(env={}), "x", {}, {})
            assert emit.call_count == 1
            cli._emit_cmd_event(service(env={"ODOO_UNATTENDED": "1"}), "x", {}, {})
            assert emit.call_count == 1
