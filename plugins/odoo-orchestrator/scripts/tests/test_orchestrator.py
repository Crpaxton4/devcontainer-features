"""Tests for orchestrator.py. Run: python3 -m unittest discover -s plugins/odoo-orchestrator/scripts/tests"""

import json
import os
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
    def test_path_is_per_task_and_round(self):
        path = orch.plan_path("42", 2)
        self.assertEqual(path, Path(self.tmp.name) / "briefs/42/round-2.md")
        self.assertTrue(path.parent.is_dir())

    def test_rejects_traversal_and_bad_round(self):
        for task, rnd in (("../x", 1), ("a/b", 1), ("", 1), ("42", 0)):
            with self.assertRaises(ValueError):
                orch.plan_path(task, rnd)


class Lint(unittest.TestCase):
    def test_requirement_level_text_is_clean(self):
        text = "Invoices must show the customer's PO number.\nAcceptance: the PO prints."
        self.assertEqual(orch.lint(text), [])

    def test_flags_files_commands_and_blocks(self):
        for text in (
            "Edit models/sale_order.py to add a field.",
            "Then run `pytest -k po` to check.",
            "```\nodoo-bin -u sale\n```",
            "$ git status",
            "Change views/report.xml",
        ):
            self.assertTrue(orch.lint(text), text)


class DeadLetters(Base):
    def test_unhandled_filters_and_attaches_kept_brief(self):
        orch.plan_path("7", 1).write_text("brief 1")
        orch.plan_path("7", 2).write_text("brief 2")
        letters = [{"job_id": "a", "task": "7"}, {"job_id": "b", "task": "8"}]
        orch.mark_handled(["b"])
        out = orch.unhandled(letters)
        self.assertEqual([x["job_id"] for x in out], ["a"])
        self.assertTrue(out[0]["kept_brief"].endswith("briefs/7/round-2.md"))

    def test_task_without_kept_brief_gets_empty_string(self):
        self.assertEqual(orch.unhandled([{"job_id": "a", "task": "9"}])[0]["kept_brief"], "")


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
                "friction": [
                    {"tool": "t", "expected": "e", "observed": "o", "did": "d"}
                ],
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

    def test_no_friction_writes_nothing(self):
        report = {"jobs": [{"task": "1", "bundle": "/nonexistent"}]}
        self.assertEqual(orch.friction(report), 0)
        self.assertFalse((Path(self.tmp.name) / "friction").exists())

    def test_records_table(self):
        rows = orch.records(self.report()).splitlines()
        self.assertEqual(len(rows), 4)
        self.assertIn("| 61 | 12 | 1.5 |", rows[2])
        self.assertIn("KILLED_BY_DEADLINE", rows[3])
        self.assertNotIn("PO field | done", rows[2])


if __name__ == "__main__":
    unittest.main()
