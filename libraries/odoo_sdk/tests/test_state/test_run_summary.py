"""Tests for the machine-derived run/session summarizer (#626).

``summarize_run_activity`` is the single pure derivation both consumers share:
``stop_task`` stores its output on the run row and the billing upload attaches
it to the timesheet entry. These tests pin the reconstructable content — the
tool-activity tally, commit sha+subject lines, branch/PR provenance, the
recorded test result, and the flattened checkpoint notes — plus the
no-length-cap policy (internal text is never routed through the chatter limit).
"""

import unittest
from datetime import datetime, timezone

from odoo_sdk.commands.command import MAX_CHATTER_BODY_CHARS
from odoo_sdk.state import EventRecord
from odoo_sdk.state.summary import summarize_run_activity

_TS = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def _event(source="agent", subject="", branch="", pr_num=0, payload=None,
           external_id=None):
    return EventRecord(
        id=None,
        source=source,
        timestamp=_TS,
        task_ids=["100"],
        repo="o/r",
        pr_num=pr_num,
        branch=branch,
        subject=subject,
        payload=payload,
        external_id=external_id,
    )


class TestSummarizeRunActivity(unittest.TestCase):
    def test_empty_inputs_yield_empty_summary(self):
        self.assertEqual(summarize_run_activity([], []), "")

    def test_tallies_agent_tool_activity(self):
        events = [
            _event(subject="task_note"),
            _event(subject="task_note"),
            _event(subject="get_task"),
        ]
        self.assertEqual(
            summarize_run_activity(events, []),
            "actions: task_note x2, get_task",
        )

    def test_claude_hook_events_count_by_source_when_subject_empty(self):
        events = [_event(source="claude:PostToolUse", subject="")]
        self.assertEqual(
            summarize_run_activity(events, []), "actions: claude:PostToolUse"
        )

    def test_commits_carry_short_sha_and_subject(self):
        events = [
            _event(
                source="commit",
                subject="fix: VAT rounding\n\nlong body",
                external_id="git:0123456789abcdef",
            )
        ]
        self.assertEqual(
            summarize_run_activity(events, []),
            "commits: 012345678 fix: VAT rounding long body",
        )

    def test_branch_pr_and_test_result_segments(self):
        events = [
            _event(subject="stop_task", branch="100#fix-vat", pr_num=42,
                   payload={"test_result": "passed"}),
        ]
        summary = summarize_run_activity(events, [])
        self.assertIn("branch 100#fix-vat", summary)
        self.assertIn("PR #42", summary)
        self.assertIn("tests: passed", summary)

    def test_pr_url_wins_over_bare_pr_number(self):
        events = [
            _event(subject="x", pr_num=42,
                   payload={"pr_url": "https://github.com/o/r/pull/42"}),
        ]
        summary = summarize_run_activity(events, [])
        self.assertIn("PR https://github.com/o/r/pull/42", summary)
        self.assertNotIn("PR #42", summary)

    def test_last_test_result_wins(self):
        events = [
            _event(subject="a", payload={"test_result": "failed"}),
            _event(subject="b", payload={"test_result": "passed"}),
        ]
        self.assertIn("tests: passed", summarize_run_activity(events, []))

    def test_notes_are_flattened_to_one_line_each(self):
        summary = summarize_run_activity(
            [], ["Plan:\n- fix rounding\n- add test", "  ", "done"]
        )
        self.assertEqual(summary, "notes: Plan: - fix rounding - add test | done")

    def test_segments_compose_in_order(self):
        events = [
            _event(subject="task_note"),
            _event(source="commit", subject="fix it", external_id="git:aaaabbbbcccc"),
        ]
        summary = summarize_run_activity(events, ["wrapped up"])
        self.assertEqual(
            summary,
            "actions: task_note; commits: aaaabbbbc fix it; notes: wrapped up",
        )

    def test_no_length_cap_is_applied(self):
        # Length policy (#626): derived summaries are internal/local text; the
        # 300-char cap applies only to chatter posts, never here.
        notes = [f"checkpoint {i} with plenty of narrative detail" for i in range(30)]
        summary = summarize_run_activity([], notes)
        self.assertGreater(len(summary), MAX_CHATTER_BODY_CHARS)
        self.assertIn("checkpoint 29", summary)

    def test_review_only_events_yield_no_action_tally(self):
        # Review/comment resync events are not agent activity; with nothing
        # else recorded the summary stays empty rather than fabricating a line.
        events = [_event(source="review", subject="LGTM pass")]
        self.assertEqual(summarize_run_activity(events, []), "")

    def test_commit_without_external_id_still_lists_subject(self):
        events = [_event(source="commit", subject="hotfix rounding")]
        self.assertEqual(
            summarize_run_activity(events, []), "commits: hotfix rounding"
        )


if __name__ == "__main__":
    unittest.main()
