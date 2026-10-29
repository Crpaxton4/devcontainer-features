"""The answers write the web UI uses."""

import json

import pytest
from odoo_dev_data.jobs import InvalidJobTransitionError, JobStore

from dispatcher import answers


def _submit(home, job_id="j1"):
    with JobStore(home=home) as store:
        store.submit(job_id=job_id, batch_id="b", task_id="1", repo="a", brief="x")
        store.mark_running(job_id)


def test_save_answers_stores_them_on_a_done_job(home):
    _submit(home)
    with JobStore(home=home) as store:
        store.record_result("j1", outcome="COMPLETED")
    given = [{"question": "q?", "accepted": True, "answer": ""}]
    job = answers.save_answers(home, "j1", given)
    assert json.loads(job.answers) == given
    with JobStore(home=home) as store:
        assert json.loads(store.get("j1").answers) == given


def test_save_answers_refuses_a_job_that_is_not_done(home):
    _submit(home)
    with pytest.raises(InvalidJobTransitionError):
        answers.save_answers(home, "j1", [])
