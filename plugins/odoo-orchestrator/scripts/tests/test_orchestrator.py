"""Tests for orchestrator.py. Run: uv run python -m unittest discover -s plugins/odoo-orchestrator/scripts/tests"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from odoo_dev_data.jobs import DeadReason, JobStore
from odoo_dev_data.tasks import read_task

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import orchestrator as orch  # noqa: E402

A, B = "a" * 32, "b" * 32
LEGACY = "legacy-task-runs"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        os.environ["ODOO_DEV_HOME"] = self.tmp.name
        self.addCleanup(os.environ.pop, "ODOO_DEV_HOME")

    def submit(self, job_id, task, batch=A, repo="r"):
        with JobStore(home=self.home) as store:
            store.submit(
                job_id=job_id, batch_id=batch, task_id=task, repo=repo, brief="b"
            )

    def finish(self, job_id, envelope=None):
        with JobStore(home=self.home) as store:
            store.mark_running(job_id)
            store.record_result(
                job_id, outcome="COMPLETED", envelope=envelope, cost_usd=1.5
            )

    def kill(self, job_id):
        with JobStore(home=self.home) as store:
            store.dead_letter(job_id, DeadReason.KILLED_BY_DEADLINE, detail="late")

    def job(self, job_id):
        with JobStore(home=self.home) as store:
            return store.get(job_id)

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = orch.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class Briefs(Base):
    def test_drafts_are_numbered_under_the_task_and_never_overwritten(self):
        code, out, _ = self.run_main("plan", "42")
        first = Path(out.strip())
        first.write_text("draft one")
        second = Path(self.run_main("plan", "42")[1].strip())
        self.assertEqual(code, 0)
        self.assertEqual(first, self.home / "tasks/42/draft-1.md")
        self.assertEqual(second.name, "draft-2.md")
        self.assertEqual(first.read_text(), "draft one")

    def test_bad_task_is_a_clean_error(self):
        for task in ("../x", "a/b", "abc"):
            self.assertEqual(self.run_main("plan", task)[0], 2, task)

    def test_approve_writes_brief_md_and_approved_prints_it(self):
        self.assertEqual(self.run_main("approved", "7")[1].strip(), "")
        drafts = [Path(self.run_main("plan", "7")[1].strip()) for _ in range(3)]
        for index, path in enumerate(drafts, 1):
            path.write_text(f"draft {index}")
        code, out, _ = self.run_main("approve", "7", str(drafts[0]))
        brief = self.home / "tasks/7/brief.md"
        self.assertEqual((code, out.strip()), (0, str(brief)))
        self.assertEqual(brief.read_text(), "draft 1")
        self.assertEqual(self.run_main("approved", "7")[1].strip(), str(brief))

    def test_approve_rejects_outside_other_and_empty_drafts(self):
        outside = self.home / "other.md"
        outside.write_text("text")
        other = Path(self.run_main("plan", "8")[1].strip())
        other.write_text("text")
        empty = Path(self.run_main("plan", "7")[1].strip())
        for path in (outside, other, empty, "/nonexistent/brief.md"):
            self.assertEqual(self.run_main("approve", "7", str(path))[0], 2, path)
        self.assertFalse((self.home / "tasks/7/brief.md").exists())

    def test_approve_job_stamps_the_row(self):
        self.submit("j1", "7")
        self.assertIsNone(self.job("j1").approved_us)
        self.assertEqual(self.run_main("approve-job", "j1")[0], 0)
        self.assertIsNotNone(self.job("j1").approved_us)
        self.assertEqual(self.run_main("approve-job", "nope")[0], 2)


class Lint(unittest.TestCase):
    def test_requirement_level_text_is_clean(self):
        text = "# Problem\n\nInvoices must show the customer's PO number.\n\n## Acceptance\nThe PO prints."
        self.assertEqual(orch.lint(text), [])

    def test_headings_and_plain_words_are_not_flagged(self):
        for text in (
            "# Problem",
            "e.g. totals.csv export",
            "README.md?",
            "node.js 3.1",
        ):
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

    def test_flags_bare_code_file_names(self):
        for text in (
            "Use the sale_order.py file",
            "Open res_partner.xml and edit",
            "Edit `sale_order.py`",
            "Change line 40 of sale_order.py",
        ):
            self.assertTrue(orch.lint(text), text)

    def test_ordinary_prose_is_not_sql(self):
        for text in (
            "The user can select a customer from the dropdown.",
            "Salespeople select the lines they want from the quote.",
            "We must update every set of prices when the list changes.",
        ):
            self.assertEqual(orch.lint(text), [], text)

    def test_flags_oversize(self):
        self.assertTrue(orch.lint("a" * 100_001))


class DeadLetters(Base):
    def listed(self):
        import json

        return json.loads(self.run_main("unhandled")[1])

    def test_letter_stays_until_handled(self):
        self.submit("a", "7")
        self.kill("a")
        self.assertEqual([x["job_id"] for x in self.listed()], ["a"])
        self.assertEqual(len(self.listed()), 1)  # a failed or skipped run marks nothing
        self.assertEqual(self.run_main("handled", "a")[0], 0)
        self.assertEqual(self.listed(), [])

    def test_repeat_failure_reappears_under_a_new_job_id(self):
        self.submit("a", "7")
        self.kill("a")
        self.run_main("handled", "a")
        self.submit("b", "7")
        self.kill("b")
        self.assertEqual([x["job_id"] for x in self.listed()], ["b"])

    def test_requeue_uses_the_approved_brief(self):
        self.submit("a", "7")
        self.kill("a")
        self.submit("c", "9")
        self.kill("c")
        draft = Path(self.run_main("plan", "7")[1].strip())
        draft.write_text("draft")
        self.run_main("approve", "7", str(draft))
        by_id = {x["job_id"]: x for x in self.listed()}
        self.assertTrue(by_id["a"]["approved_brief"].endswith("tasks/7/brief.md"))
        self.assertEqual(by_id["c"]["approved_brief"], "")
        self.assertEqual((by_id["a"]["repo"], by_id["a"]["round"]), ("r", 1))


class Reports(Base):
    ENVELOPE = {
        "structured_output": {
            "summary": "Added the PO field | done",
            "friction": [{"tool": "t", "expected": "e", "observed": "o", "did": "d"}],
        }
    }

    def seed(self):
        self.submit("j1", "7")
        self.submit("j2", "8")
        self.finish("j1", self.ENVELOPE)
        self.kill("j2")

    def test_friction_lands_in_task_json_once(self):
        self.seed()
        self.assertEqual(self.run_main("friction", A)[1].strip(), "1")
        entries = read_task(7, self.home).friction
        self.assertEqual([(e["observed"], e["job_id"]) for e in entries], [("o", "j1")])
        self.assertIsNotNone(self.job("j1").friction_recorded_us)
        self.assertEqual(self.run_main("friction", A)[1].strip(), "0")
        self.assertEqual(len(read_task(7, self.home).friction), 1)

    def test_no_friction_marks_and_writes_nothing(self):
        self.submit("j1", "7")
        self.finish("j1", {"structured_output": {"summary": "x", "friction": []}})
        self.assertEqual(self.run_main("friction", A)[1].strip(), "0")
        self.assertIsNone(self.job("j1").friction_recorded_us)
        self.assertFalse((self.home / "tasks/7/task.json").exists())

    def test_records_table(self):
        self.seed()
        rows = self.run_main("records", A)[1].splitlines()
        self.assertEqual(len(rows), 4)
        self.assertIn("| 7 | j1 | DONE | COMPLETED |", rows[2])
        self.assertIn("| 1.5 |", rows[2])
        self.assertNotIn("PO field | done", rows[2])
        self.assertIn("KILLED_BY_DEADLINE", rows[3])

    def test_records_with_no_envelope_has_blank_summary(self):
        self.submit("j1", "7")
        self.finish("j1")
        self.assertTrue(self.run_main("records", A)[1].splitlines()[2].endswith("|  |"))

    def test_bad_batch_id_is_a_clean_error(self):
        for verb in ("friction", "records", "batch-done"):
            self.assertEqual(self.run_main(verb, "../x")[0], 2, verb)

    def test_dead_letter_stays_unhandled_after_batch_done(self):
        self.seed()
        self.assertEqual(self.run_main("batch-done", A)[0], 0)
        self.assertIsNotNone(self.job("j2").reported_us)
        self.assertIsNone(self.job("j2").handled_us)
        listed = json.loads(self.run_main("unhandled")[1])
        self.assertEqual([x["job_id"] for x in listed], ["j2"])

    def test_handled_removes_the_letter_and_keeps_the_batch_pending(self):
        self.seed()
        self.assertEqual(self.run_main("handled", "j2")[0], 0)
        self.assertEqual(json.loads(self.run_main("unhandled")[1]), [])
        self.assertIsNone(self.job("j2").reported_us)
        self.assertEqual(self.run_main("batches")[1].split(), [A])

    def test_legacy_batch_id_is_accepted_by_every_batch_verb(self):
        self.submit("j1", "7", batch=LEGACY)
        self.finish("j1")
        self.assertEqual(self.run_main("friction", LEGACY)[0], 0)
        self.assertEqual(self.run_main("records", LEGACY)[0], 0)
        self.assertIn("| 7 | j1 |", self.run_main("records", LEGACY)[1])
        self.assertEqual(self.run_main("batches")[1].split(), [LEGACY])
        self.assertEqual(self.run_main("batch-done", LEGACY)[0], 0)
        self.assertEqual(self.run_main("batches")[1].strip(), "")
        self.assertIsNotNone(self.job("j1").reported_us)

    def test_near_legacy_ids_are_refused(self):
        for verb in ("friction", "records", "batch-done"):
            for bad in (LEGACY + "/..", "../" + LEGACY, LEGACY.upper()):
                self.assertEqual(self.run_main(verb, bad)[0], 2, (verb, bad))


class Batches(Base):
    def test_batch_is_listed_when_finished_until_done(self):
        self.submit("a", "1", batch=A)
        self.submit("b", "2", batch=B)
        self.assertEqual(self.run_main("batches")[1].strip(), "")  # still PENDING
        self.finish_pair()
        self.assertEqual(self.run_main("batches")[1].split(), [A, B])
        self.assertEqual(self.run_main("batch-done", A)[0], 0)
        self.assertEqual(self.run_main("batches")[1].split(), [B])
        self.assertIsNotNone(self.job("a").reported_us)

    def finish_pair(self):
        self.finish("a")
        self.kill("b")

    def test_extra_argument_is_usage_error(self):
        self.assertEqual(self.run_main("batches", "x")[0], 2)


if __name__ == "__main__":
    unittest.main()
