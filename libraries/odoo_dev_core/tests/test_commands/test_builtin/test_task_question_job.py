"""``task_question`` counts the question on the dispatcher job it runs under."""

import unittest
from unittest.mock import MagicMock, patch

from ...fakes import FakeJobs, service

_QUESTION = "odoo_dev_core.builtins.task_question"


def _post(**services):
    with (
        patch(f"{_QUESTION}.get_task_detail", return_value={"name": "T"}),
        patch(f"{_QUESTION}.post_chatter_note", return_value=9),
    ):
        return service(client=MagicMock(), **services).execute(
            "task_question", task_id=1, question="which?"
        )


class TestTaskQuestionJobCounter(unittest.TestCase):
    def test_increments_the_job_and_reports_its_id(self):
        jobs = FakeJobs()
        result = _post(jobs=jobs, env={"ODOO_JOB_ID": "job-1"})
        self.assertEqual(jobs.questions, {"job-1": 1})
        self.assertEqual(result["job_id"], "job-1")

    def test_no_job_id_in_env_leaves_the_counter_alone(self):
        jobs = FakeJobs()
        result = _post(jobs=jobs, env={})
        self.assertEqual(jobs.questions, {})
        self.assertNotIn("job_id", result)

    def test_no_job_ledger_is_tolerated(self):
        result = _post(jobs=None, env={"ODOO_JOB_ID": "job-1"})
        self.assertNotIn("job_id", result)

    def test_a_deduplicated_replay_does_not_count_again(self):
        jobs = FakeJobs()
        env = {"ODOO_JOB_ID": "job-1"}
        db = service().services.events
        with (
            patch(f"{_QUESTION}.get_task_detail", return_value={"name": "T"}),
            patch(f"{_QUESTION}.post_chatter_note", return_value=9),
        ):
            svc = service(client=MagicMock(), jobs=jobs, env=env, events=db)
            svc.execute("task_question", task_id=1, question="q", dedupe_key="k")
            replay = svc.execute(
                "task_question", task_id=1, question="q", dedupe_key="k"
            )
        self.assertTrue(replay["deduplicated"])
        self.assertEqual(jobs.questions, {"job-1": 1})


if __name__ == "__main__":
    unittest.main()
