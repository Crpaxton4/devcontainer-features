"""Frozen snapshot of the default MCP tool surface (#712, guards #715).

The two name lists below are deliberate LITERALS — never derived from the
code under test — freezing the 27-tool default surface and the 16 gated
names as they stand before the gating migration (#715). That migration may
move the gating mechanism but MUST NOT change either set, and MUST NOT edit
this file: a diff here is a wire-surface change, not a refactor.
"""

import unittest
from unittest.mock import Mock

from odoo_sdk.commands import Registry
from odoo_sdk.mcp.tools import (
    GATED_TOOL_NAMES,
    build_explicit_tools,
    default_tool_surface,
)

#: The 27 tools an out-of-the-box server exposes. FROZEN — see module docstring.
FROZEN_DEFAULT_TOOL_NAMES = frozenset(
    {
        "create_task",
        "get_activities",
        "get_task",
        "get_task_attachments",
        "get_task_chatter",
        "get_tasks",
        "get_todo",
        "get_uid",
        "mark_activity_done",
        "read_attachment",
        "read_knowledge_article",
        "resume_task",
        "schedule_activity",
        "search_activity_types",
        "search_chatter",
        "search_knowledge_articles",
        "search_projects",
        "search_tasks",
        "start_task",
        "stop_task",
        "task_aging",
        "task_list",
        "task_note",
        "task_question",
        "task_status",
        "timesheet_summary",
        "unbilled_hours",
    }
)

#: The 16 narrow-context tools held back by default. FROZEN — see module
#: docstring.
FROZEN_GATED_TOOL_NAMES = frozenset(
    {
        "abort_run",
        "abort_task",
        "assign_event",
        "discover_runs",
        "get_mail_status",
        "get_models",
        "list_runs",
        "normalize_timesheets",
        "optimize_sessions",
        "query_sessions",
        "report_runs",
        "resync",
        "search_count",
        "stop_all",
        "stop_run",
        "unlogged_time_report",
    }
)


class TestDefaultSurfaceSnapshot(unittest.TestCase):
    """The served surface still matches the frozen 27 + 16 name sets."""

    def test_frozen_literals_have_the_expected_sizes(self):
        # Self-check on the literals so a botched edit to this file cannot
        # accidentally weaken the snapshot.
        self.assertEqual(len(FROZEN_DEFAULT_TOOL_NAMES), 27)
        self.assertEqual(len(FROZEN_GATED_TOOL_NAMES), 16)
        self.assertFalse(FROZEN_DEFAULT_TOOL_NAMES & FROZEN_GATED_TOOL_NAMES)

    def test_default_surface_is_exactly_the_frozen_27(self):
        surface = default_tool_surface(
            build_explicit_tools(Registry(Mock())), include_gated=False
        )
        self.assertEqual(set(surface), FROZEN_DEFAULT_TOOL_NAMES)

    def test_gated_set_is_exactly_the_frozen_16(self):
        self.assertEqual(set(GATED_TOOL_NAMES), FROZEN_GATED_TOOL_NAMES)

    def test_full_surface_is_the_union_of_both_frozen_sets(self):
        full = default_tool_surface(
            build_explicit_tools(Registry(Mock())), include_gated=True
        )
        self.assertEqual(set(full), FROZEN_DEFAULT_TOOL_NAMES | FROZEN_GATED_TOOL_NAMES)


if __name__ == "__main__":
    unittest.main()
