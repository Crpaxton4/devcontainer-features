"""Tests for the machine-derived run/session summarizers (#626, #710).

``summarize_run_activity`` is the single pure derivation both consumers share:
``stop_task`` stores its output on the run row and the billing upload attaches
it to the timesheet entry. These tests pin the reconstructable content — the
tool-activity tally, commit sha+subject lines, branch/PR provenance, the
recorded test result, and the flattened checkpoint notes — plus the
no-length-cap policy (internal text is never routed through the chatter limit).

``summarize_session_context`` (#710) is its narrative counterpart, reading the
same events for WHAT WAS DONE rather than how many tools ran. Its tests pin the
three-tier preference order (chatter notes > commits/PRs/reviews > hook
context), the per-item headline cap, and that each tier reads only fields that
are actually persisted on an event row.
"""

import unittest
from datetime import datetime, timezone

from odoo_sdk.commands.command import MAX_CHATTER_BODY_CHARS
from odoo_sdk.state import EventRecord
from odoo_sdk.state.summary import (
    _HEADLINE_ITEM_CHARS,
    summarize_run_activity,
    summarize_session_context,
)

_TS = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def _event(
    source="agent", subject="", branch="", pr_num=0, payload=None, external_id=None
):
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
            _event(
                subject="stop_task",
                branch="100#fix-vat",
                pr_num=42,
                payload={"test_result": "passed"},
            ),
        ]
        summary = summarize_run_activity(events, [])
        self.assertIn("branch 100#fix-vat", summary)
        self.assertIn("PR #42", summary)
        self.assertIn("tests: passed", summary)

    def test_pr_url_wins_over_bare_pr_number(self):
        events = [
            _event(
                subject="x",
                pr_num=42,
                payload={"pr_url": "https://github.com/o/r/pull/42"},
            ),
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
        # chatter cap applies only to chatter posts, never here.
        notes = [f"checkpoint {i} with plenty of narrative detail" for i in range(30)]
        summary = summarize_run_activity([], notes)
        self.assertGreater(len(summary), MAX_CHATTER_BODY_CHARS)
        self.assertIn("checkpoint 29", summary)

    def test_interim_notes_read_the_same_as_posted_ones(self):
        # #901: interim notes reach the run via the SAME ``append_note`` row a
        # posted note writes, so the derivation sees one undifferentiated list
        # of notes. That is the point — a checkpoint captured locally is still
        # in the summary ``stop_task`` stores, so nothing is lost by not
        # posting it. The summarizer is therefore deliberately unaware of the
        # distinction, and this test pins that.
        notes = ["plan: split the parser", "parser done", "shipped in #901"]
        summary = summarize_run_activity([], notes)
        self.assertEqual(
            summary,
            "notes: plan: split the parser | parser done | shipped in #901",
        )

    def test_review_only_events_yield_no_action_tally(self):
        # Review/comment resync events are not agent activity; with nothing
        # else recorded the summary stays empty rather than fabricating a line.
        events = [_event(source="review", subject="LGTM pass")]
        self.assertEqual(summarize_run_activity(events, []), "")

    def test_commit_without_external_id_still_lists_subject(self):
        events = [_event(source="commit", subject="hotfix rounding")]
        self.assertEqual(summarize_run_activity(events, []), "commits: hotfix rounding")


class TestSummarizeSessionContext(unittest.TestCase):
    """The narrative headline a timesheet row leads with (#710).

    Strict preference order, first non-empty tier wins: chatter notes the user
    wrote, then commits / PR titles / review states, then the hook context
    (first user prompt, else ``cwd``).
    """

    def test_nothing_to_tell_yields_empty(self):
        self.assertEqual(summarize_session_context([]), "")

    def test_tool_tallies_are_never_the_headline(self):
        # The headline complaint of #710: 231 Bash calls say nothing billable,
        # so an agent/hook tally alone contributes NO headline at all and the
        # caller falls back (the tally still reaches the row as the debug tail).
        events = [_event(subject="Bash") for _ in range(231)]
        self.assertEqual(summarize_session_context(events), "")

    def test_chatter_note_first_lines_lead(self):
        events = [
            _event(source="chatter", subject="Reconciled the July VAT postings"),
            _event(source="chatter", subject="Raised the rounding fix with finance"),
        ]
        self.assertEqual(
            summarize_session_context(events),
            "Reconciled the July VAT postings | Raised the rounding fix with finance",
        )

    def test_chatter_beats_commits_and_hook_context(self):
        events = [
            _event(source="commit", subject="fix: rounding"),
            _event(source="chatter", subject="Explained the fix to the client"),
            _event(
                source="claude:UserPromptSubmit", payload={"prompt": "fix rounding"}
            ),
        ]
        self.assertEqual(
            summarize_session_context(events), "Explained the fix to the client"
        )

    def test_repeated_chatter_line_is_listed_once(self):
        events = [_event(source="chatter", subject="same note") for _ in range(3)]
        self.assertEqual(summarize_session_context(events), "same note")

    def test_blank_chatter_subject_falls_through_to_the_next_tier(self):
        # Pre-#710 chatter rows were stored with an empty subject (the puller
        # read only ``subject``, which a logged note leaves blank), so they must
        # not shadow the forge tier with an empty headline.
        events = [
            _event(source="chatter", subject=""),
            _event(source="commit", subject="fix: VAT rounding"),
        ]
        self.assertEqual(summarize_session_context(events), "commit: fix: VAT rounding")

    def test_commit_subjects_and_pr_titles(self):
        events = [
            _event(source="commit", subject="feat: add the reconciliation wizard"),
            _event(source="pr_opened", subject="Add reconciliation wizard", pr_num=42),
            _event(source="merge", subject="Add reconciliation wizard", pr_num=42),
        ]
        self.assertEqual(
            summarize_session_context(events),
            "commit: feat: add the reconciliation wizard, "
            "PR #42 opened: Add reconciliation wizard, "
            "PR #42 merged: Add reconciliation wizard",
        )

    def test_review_state_names_the_verdict(self):
        events = [
            _event(source="review", pr_num=7, payload={"review_state": "APPROVED"})
        ]
        self.assertEqual(summarize_session_context(events), "PR #7 reviewed (APPROVED)")

    def test_review_without_recorded_state_still_names_the_pr(self):
        # Reviews resynced before #710 carry no ``review_state`` payload; they
        # must still say which PR was reviewed rather than nothing.
        events = [_event(source="review", pr_num=7)]
        self.assertEqual(summarize_session_context(events), "PR #7 reviewed")

    def test_authored_comment_is_summarised_as_review_family(self):
        events = [_event(source="comment", pr_num=9)]
        self.assertEqual(summarize_session_context(events), "PR #9 commented")

    def test_commit_subject_is_the_first_line_only(self):
        events = [_event(source="commit", subject="fix: rounding\n\nlong body text")]
        self.assertEqual(summarize_session_context(events), "commit: fix: rounding")

    def test_hook_only_session_is_named_by_its_first_prompt(self):
        events = [
            _event(source="claude:SessionStart", payload={"cwd": "/workspaces/acme"}),
            _event(
                source="claude:UserPromptSubmit",
                payload={"prompt": "Add a VAT column to the invoice report"},
            ),
            _event(source="claude:PreToolUse", subject="Bash"),
        ]
        self.assertEqual(
            summarize_session_context(events),
            "prompt: Add a VAT column to the invoice report",
        )

    def test_hook_only_session_falls_back_to_cwd(self):
        events = [
            _event(source="claude:SessionStart", payload={"cwd": "/workspaces/acme"}),
            _event(source="claude:PreToolUse", subject="Bash"),
        ]
        self.assertEqual(summarize_session_context(events), "cwd /workspaces/acme")

    def test_first_prompt_wins_over_later_ones(self):
        events = [
            _event(source="claude:UserPromptSubmit", payload={"prompt": "first ask"}),
            _event(source="claude:UserPromptSubmit", payload={"prompt": "second ask"}),
        ]
        self.assertEqual(summarize_session_context(events), "prompt: first ask")

    def test_long_headline_item_is_capped_and_marked(self):
        # A multi-KB markdown note must not become a multi-KB timesheet name.
        note = "x" * (_HEADLINE_ITEM_CHARS * 4)
        headline = summarize_session_context([_event(source="chatter", subject=note)])
        self.assertTrue(headline.endswith("..."))
        self.assertLessEqual(len(headline), _HEADLINE_ITEM_CHARS + 3)

    def test_non_dict_payload_never_raises(self):
        # The payload column is free-form JSON; a scalar there must degrade to
        # "no context", not abort the billing description derivation.
        events = [_event(source="claude:SessionStart", payload=["not", "a", "dict"])]
        self.assertEqual(summarize_session_context(events), "")


if __name__ == "__main__":
    unittest.main()
