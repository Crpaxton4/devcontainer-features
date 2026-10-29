"""The one write the web UI makes: save the user's answers to a job's questions.

``dispatcher.reporting`` is read-only, so this small module holds the write.
It opens ``state.db`` through :class:`odoo_dev_data.jobs.JobStore`, saves, and closes.
"""

from pathlib import Path

from odoo_dev_data.jobs import Job, JobStore


def save_answers(home: Path, job_id: str, answers: list[dict]) -> Job:
    """Store ``answers`` on a DONE job; raise InvalidJobTransitionError otherwise."""
    with JobStore(home=home) as store:
        return store.save_answers(job_id, answers)
