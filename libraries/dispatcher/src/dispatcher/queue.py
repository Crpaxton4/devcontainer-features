"""Submission, ordering and startup reconciliation (design sections 7 and 8).

A job is three values: a repo, a task id and a path to the brief. The whole
approved set arrives as **one JSON array argument**, each element::

    {"repo": "<repo>", "task": "<task id>", "brief": "<path readable as text>"}

and optionally ``"round": <int >= 1>``. There is no batch file. The brief path
is dereferenced here, on the host, once; the text goes into the job row and the
path is forgotten (the file may be deleted at once; a re-dispatch uses the
stored text). The whole call is validated before anything is written, so a bad
element rejects the call and no job of it exists.
"""

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from odoo_sdk.state.jobs import (
    DeadReason,
    DuplicateTaskInFlightError,
    Job,
    JobState,
    JobStore,
)

from .deadletter import STATEMENTS

_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class SubmissionError(ValueError):
    """The submitted JSON array is malformed. Nothing was stored."""


@dataclass(frozen=True)
class JobSpec:
    """One validated submission element, brief already dereferenced."""

    repo: str
    task_id: str
    brief: str
    round: int = 1


def _element(index: int, raw: object) -> JobSpec:
    where = f"element {index}"
    if not isinstance(raw, dict):
        raise SubmissionError(f"{where} is not an object")
    unknown = set(raw) - {"repo", "task", "brief", "round"}
    if unknown:
        raise SubmissionError(f"{where} has unknown keys: {sorted(unknown)}")
    repo, task, path = raw.get("repo"), raw.get("task"), raw.get("brief")
    for name, value in (("repo", repo), ("task", task)):
        if not isinstance(value, str) or not _COMPONENT.match(value):
            raise SubmissionError(
                f"{where}: {name} must be one plain name, got {value!r}"
            )
    if not isinstance(path, str) or not path:
        raise SubmissionError(f"{where}: brief must be a path")
    rnd = raw.get("round", 1)
    if not isinstance(rnd, int) or isinstance(rnd, bool) or rnd < 1:
        raise SubmissionError(f"{where}: round must be an integer >= 1")
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SubmissionError(f"{where}: brief {path!r} is not readable as text: {exc}")
    if not text.strip():
        raise SubmissionError(f"{where}: brief {path!r} is empty")
    return JobSpec(repo=repo, task_id=task, brief=text, round=rnd)


def parse_submission(argument: str) -> list[JobSpec]:
    """Validate the one JSON-array argument and dereference every brief path."""
    try:
        data = json.loads(argument)
    except ValueError as exc:
        raise SubmissionError(f"the argument is not valid JSON: {exc}") from exc
    if not isinstance(data, list) or not data:
        raise SubmissionError("the argument must be a non-empty JSON array")
    return [_element(index, raw) for index, raw in enumerate(data)]


def order_by_project(specs: list[JobSpec]) -> list[JobSpec]:
    """Group jobs of one repo together, keeping first-appearance order.

    Jobs in one project share a container, so admitting them together avoids
    repeated cold starts. An ordering the queue may apply, nothing more.
    """
    first_seen: dict[str, int] = {}
    for spec in specs:
        first_seen.setdefault(spec.repo, len(first_seen))
    return sorted(specs, key=lambda spec: first_seen[spec.repo])  # stable


def new_id() -> str:
    return uuid.uuid4().hex


def submit(store: JobStore, specs: list[JobSpec], batch_id: str) -> list[Job]:
    """Store each spec as PENDING; a live twin becomes a DUPLICATE dead letter."""
    jobs = []
    for spec in order_by_project(specs):
        fields = dict(
            job_id=new_id(),
            batch_id=batch_id,
            task_id=spec.task_id,
            repo=spec.repo,
            brief=spec.brief,
            round=spec.round,
        )
        try:
            jobs.append(store.submit(**fields))
        except DuplicateTaskInFlightError:
            jobs.append(
                store.reject_duplicate(
                    **fields,
                    detail=f"task {spec.task_id} already has a PENDING or RUNNING job",
                )
            )
    return jobs


def reconcile(store: JobStore) -> list[str]:
    """Startup: every RUNNING record is lying; dead-letter it as ORPHANED_BY_RESTART.

    Runs before anything is admitted, or the duplicate gate would block those
    tasks forever.
    """
    return store.reconcile_orphans(detail=STATEMENTS[DeadReason.ORPHANED_BY_RESTART])


def pending_jobs(store: JobStore) -> list[Job]:
    """PENDING jobs, grouped by project, oldest first within a project."""
    jobs = store.list_by_state(JobState.PENDING)
    first_seen: dict[str, int] = {}
    for job in jobs:
        first_seen.setdefault(job.repo, len(first_seen))
    return sorted(jobs, key=lambda job: first_seen[job.repo])


def live_repos(store: JobStore) -> list[str]:
    """Repos with a PENDING or RUNNING job: the queue's in-flight projects."""
    jobs = store.list_by_state(JobState.PENDING) + store.list_by_state(JobState.RUNNING)
    return sorted({job.repo for job in jobs})
