"""Tests for orchestrator.py. Run: python3 -m unittest discover -s plugins/odoo-orchestrator/scripts/tests"""

import json
import os
import stat
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import orchestrator as orch  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["ODOO_ORCHESTRATOR_HOME"] = self.tmp.name
        self.addCleanup(os.environ.pop, "ODOO_ORCHESTRATOR_HOME")


class PlanPath(Base):
    def test_drafts_are_numbered_and_never_overwritten(self):
        first = orch.plan_path("42")
        first.write_text("draft one")
        second = orch.plan_path("42")
        self.assertEqual(first, Path(self.tmp.name) / "briefs/42/draft-1.md")
        self.assertEqual(second.name, "draft-2.md")
        self.assertEqual(first.read_text(), "draft one")

    def test_rejects_traversal(self):
        for task in ("../x", "a/b", ""):
            with self.assertRaises(ValueError):
                orch.plan_path(task)

    def test_relative_home_becomes_absolute(self):
        os.environ["ODOO_ORCHESTRATOR_HOME"] = "rel/home"
        self.assertTrue(orch.home().is_absolute())

    def test_unwritable_home_is_a_clean_error(self):
        locked = Path(self.tmp.name) / "locked"
        locked.mkdir()
        locked.chmod(stat.S_IREAD | stat.S_IEXEC)
        self.addCleanup(locked.chmod, stat.S_IRWXU)
        os.environ["ODOO_ORCHESTRATOR_HOME"] = str(locked / "home")
        if os.access(locked, os.W_OK):
            self.skipTest("running as a user who ignores permissions")
        self.assertEqual(orch.main(["plan-path", "7"]), 2)

    def test_bad_task_is_a_clean_error(self):
        self.assertEqual(orch.main(["plan-path", "a/b"]), 2)


class Lint(unittest.TestCase):
    def test_requirement_level_text_is_clean(self):
        text = "# Problem\n\nInvoices must show the customer's PO number.\n\n## Acceptance\nThe PO prints."
        self.assertEqual(orch.lint(text), [])

    def test_headings_and_plain_words_are_not_flagged(self):
        for text in ("# Problem", "e.g. totals.csv export", "README.md?", "node.js 3.1"):
            self.assertEqual(orch.lint(text), [], text)

    def test_flags_files_commands_sql_xpath_and_blocks(self):
        for text in (
            "Edit models/sale_order.py to add a field.",
            "Change views/report.html",
            "Look at static/src/app.tsx",
            "Then run `pytest -k po` to check.",
            "Run odoo-bin -u sale_custom -d test",
            "docker compose exec odoo odoo -u sale",
            "```\nx\n```",
            "$ git status",
            "SELECT id FROM account_move WHERE x",
            "use //field[@name='phone']",
        ):
            self.assertTrue(orch.lint(text), text)

    def test_flags_oversize(self):
        self.assertTrue(orch.lint("a" * 100_001))


class Approval(Base):
    def test_requeue_uses_the_approved_brief_not_the_highest_draft(self):
        drafts = [orch.plan_path("7") for _ in range(3)]
        for index, path in enumerate(drafts, 1):
            path.write_text(f"draft {index}")
        orch.approve("7", str(drafts[0]))
        out = orch.unhandled([{"job_id": "a", "task": "7"}])
        self.assertTrue(out[0]["approved_brief"].endswith("briefs/7/draft-1.md"))

    def test_no_approval_means_no_brief(self):
        orch.plan_path("9").write_text("never approved")
        self.assertEqual(orch.unhandled([{"job_id": "a", "task": "9"}])[0]["approved_brief"], "")

    def test_approve_rejects_a_missing_file(self):
        with self.assertRaises(ValueError):
            orch.approve("7", "/nonexistent/brief.md")


class DeadLetters(Base):
    def test_letter_stays_until_handled(self):
        letters = [{"job_id": "a", "task": "7"}]
        self.assertEqual(len(orch.unhandled(letters)), 1)
        self.assertEqual(len(orch.unhandled(letters)), 1)  # a failed or skipped run marks nothing
        orch.mark_handled(["a"])
        self.assertEqual(orch.unhandled(letters), [])

    def test_repeat_failure_reappears_under_a_new_job_id(self):
        orch.mark_handled(["a"])
        out = orch.unhandled([{"job_id": "a", "task": "7"}, {"job_id": "b", "task": "7"}])
        self.assertEqual([x["job_id"] for x in out], ["b"])

    def test_ledger_tolerates_a_non_utf8_byte(self):
        (Path(self.tmp.name) / "handled.txt").write_bytes(b"a\n\xff\xfe\nb\n")
        out = orch.unhandled([{"job_id": "a", "task": "1"}, {"job_id": "c", "task": "1"}])
        self.assertEqual([x["job_id"] for x in out], ["c"])


class Reports(Base):
    def report(self):
        bundle = Path(self.tmp.name) / "bundle"
        bundle.mkdir()
        envelope = {
            "duration_ms": 61000,
            "num_turns": 12,
            "total_cost_usd": 1.5,
            "structured_output": {
                "summary": "Added the PO field | done",
                "friction": [{"tool": "t", "expected": "e", "observed": "o", "did": "d"}],
            },
        }
        (bundle / "envelope.json").write_text(json.dumps(envelope))
        job = {"task": "7", "repo": "r", "round": 1, "state": "DONE",
               "outcome": "DONE", "session_id": "s1", "job_id": "j1",
               "bundle": str(bundle)}
        dead = {"task": "8", "repo": "r", "round": 1, "state": "DEAD_LETTER",
                "dead_reason": "KILLED_BY_DEADLINE", "job_id": "j2"}
        return {"batch_id": "b", "jobs": [job, dead]}

    def test_friction_appends_dated_file(self):
        self.assertEqual(orch.friction(self.report(), today=date(2026, 10, 16)), 1)
        text = (Path(self.tmp.name) / "friction/2026-10-16.md").read_text()
        self.assertIn("task 7 job j1", text)
        self.assertIn("- observed: o", text)

    def test_friction_is_idempotent(self):
        report = self.report()
        self.assertEqual(orch.friction(report, today=date(2026, 10, 16)), 1)
        self.assertEqual(orch.friction(report, today=date(2026, 10, 16)), 0)
        text = (Path(self.tmp.name) / "friction/2026-10-16.md").read_text()
        self.assertEqual(text.count("## task 7"), 1)

    def test_no_friction_writes_nothing(self):
        report = {"jobs": [{"task": "1", "job_id": "x", "bundle": "/nonexistent"}]}
        self.assertEqual(orch.friction(report), 0)
        self.assertFalse((Path(self.tmp.name) / "friction").exists())

    def test_records_table(self):
        rows = orch.records(self.report()).splitlines()
        self.assertEqual(len(rows), 4)
        self.assertIn("| 61 | 12 | 1.5 |", rows[2])
        self.assertIn("KILLED_BY_DEADLINE", rows[3])
        self.assertNotIn("PO field | done", rows[2])

    def test_records_with_a_missing_envelope(self):
        job = {"task": "7", "repo": "r", "round": 1, "state": "DONE", "bundle": "/nonexistent"}
        rows = orch.records({"jobs": [job]}).splitlines()
        self.assertEqual(len(rows), 3)
        self.assertIn("| 7 | r | 1 | DONE |", rows[2])

    def test_report_is_read_from_a_file(self):
        path = Path(self.tmp.name) / "run.json"
        path.write_text(json.dumps(self.report()))
        self.assertEqual(orch.main(["records", str(path)]), 0)
        self.assertEqual(orch.main(["records", str(path) + ".missing"]), 2)


if __name__ == "__main__":
    unittest.main()
