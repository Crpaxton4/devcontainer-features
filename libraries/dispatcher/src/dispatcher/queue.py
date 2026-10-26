"""Submission, ordering and startup reconciliation (design sections 7 and 8).

A job is three values: a repo, a task id and a path to the brief. The whole
approved set arrives as **one JSON array argument**, each element::

    {"repo": "<repo>", "task": "<task id>", "brief": "<path readable as text>"}

and optionally ``"round": <int >= 1>`` and ``"entry"``, one of
``ALLOWED_ENTRIES`` (the job's slash command; default ``DISPATCH_ENTRY``). There is no batch file. The brief path
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

from odoo_client.state.jobs import (
    DeadReason,
    DuplicateTaskInFlightError,
    Job,
    JobState,
    JobStore,
)

from odoo_client.state.dispatch_state import resolve_now_us
from odoo_client.state.runs_store import RunStore

from .collect import StreamParser, read_task_record, record_then_collect, worktree_of
from .deadletter import STATEMENTS
from .settings import ALLOWED_ENTRIES
from .lifecycle import INSPECT_TIMEOUT_S, container_name, transcript_path
from .reap import host_worktree

#: Linux caps one argv string at 128 KiB (MAX_ARG_STRLEN); the prompt
#: ``<entry> <task>\n\n<brief>`` is one. Over it, exec fails with E2BIG on every
#: retry. The 11 KB under the cap covers the entry, which is a short command
#: name, so only ``<task>\n\n<brief>`` is counted.
MAX_PROMPT_BYTES = 120_000

#: A task id is one plain name. It never starts with a dot.
_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
#: A repo name may start with one dot (``.github``). A letter or digit must
#: follow, so ``.`` and ``..`` never match.
_REPO_NAME = re.compile(r"^\.?[A-Za-z0-9][A-Za-z0-9._-]*$")


class SubmissionError(ValueError):
    """The submitted JSON array is malformed. Nothing was stored."""


@dataclass(frozen=True)
class JobSpec:
    """One validated submission element, brief already dereferenced."""

    repo: str
    task_id: str
    brief: str
    round: int = 1
    entry: str = ""


def _check_entry(where: str, entry: object) -> str:
    if entry == "":
        return ""
    if not isinstance(entry, str) or entry not in ALLOWED_ENTRIES:
        raise SubmissionError(
            f"{where}: entry must be one of {list(ALLOWED_ENTRIES)}, got {entry!r}"
        )
    return entry


def _check_name(where: str, name: str, value: object, pattern: re.Pattern) -> None:
    if not isinstance(value, str) or not pattern.match(value):
        raise SubmissionError(f"{where}: {name} must be one plain name, got {value!r}")


def _element(index: int, raw: object) -> JobSpec:
    where = f"element {index}"
    if not isinstance(raw, dict):
        raise SubmissionError(f"{where} is not an object")
    unknown = set(raw) - {"repo", "task", "brief", "round", "entry"}
    if unknown:
        raise SubmissionError(f"{where} has unknown keys: {sorted(unknown)}")
    repo, task, path = raw.get("repo"), raw.get("task"), raw.get("brief")
    _check_name(where, "repo", repo, _REPO_NAME)
    _check_name(where, "task", task, _COMPONENT)
    if not isinstance(path, str) or not path:
        raise SubmissionError(f"{where}: brief must be a path")
    rnd = raw.get("round", 1)
    if not isinstance(rnd, int) or isinstance(rnd, bool) or rnd < 1:
        raise SubmissionError(f"{where}: round must be an integer >= 1")
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:  # ValueError: NUL in the path, bad UTF-8
        raise SubmissionError(f"{where}: brief {path!r} is not readable as text: {exc}")
    if not text.strip():
        raise SubmissionError(f"{where}: brief {path!r} is empty")
    if "\0" in text:
        raise SubmissionError(f"{where}: brief {path!r} contains a NUL character")
    if len(f"{task}\n\n{text}".encode()) > MAX_PROMPT_BYTES:
        raise SubmissionError(
            f"{where}: brief {path!r} is too large to pass on a command line "
            f"(limit {MAX_PROMPT_BYTES} bytes)"
        )
    entry = _check_entry(where, raw.get("entry", ""))
    return JobSpec(repo=repo, task_id=task, brief=text, round=rnd, entry=entry)


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
            entry=spec.entry,
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


def _orphan_facts(job: Job, record: dict, shell, settings, env) -> list[str]:
    """What can be seen of an orphan's container and worktree, and what cannot."""
    facts = []
    if shell is None or settings is None:
        return ["the container and worktree were not inspected"]
    name = container_name(job.repo)
    done = shell.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", name],
        env=env,
        timeout=INSPECT_TIMEOUT_S,
    )
    if done.returncode == 0:
        state = "running" if done.stdout.strip() == "true" else "not running"
        facts.append(
            f"container {name} is {state}; a session process of the dead "
            "dispatcher may still be alive inside it"
        )
    else:
        facts.append(
            f"container {name} could not be inspected (exit {done.returncode}); "
            "a session process may still be alive"
        )
    worktree = worktree_of(record)
    host = host_worktree(worktree, settings.workspace(job.repo)) if worktree else None
    if host is None:
        facts.append("no worktree is recorded in task.json")
    else:
        where = "exists and was left in place" if host.is_dir() else "does not exist"
        facts.append(f"worktree {host} {where}")
    return facts


def reconcile(
    store: JobStore,
    runs: RunStore | None = None,
    shell=None,
    settings=None,
    env: dict[str, str] | None = None,
) -> list[str]:
    """Startup: every RUNNING record is lying; dead-letter it as ORPHANED_BY_RESTART.

    Runs before anything is admitted, or the duplicate gate would block those
    tasks forever. Each orphan's container and worktree are inspected where
    possible and what could not be seen is said so. An orphan that had a
    session id gets a FAILED run row (that time was really spent), and every
    orphan gets a bundle. Nothing here stops a container or removes a worktree.
    """
    ids = []
    for job in store.list_by_state(JobState.RUNNING):
        record = read_task_record(job.job_id, job.task_id)
        facts = _orphan_facts(job, record, shell, settings, env)
        if runs is not None:
            facts.append(_record_orphan(runs, job, record, settings))
        detail = " ".join(
            [STATEMENTS[DeadReason.ORPHANED_BY_RESTART], "; ".join(facts) + "."]
        )
        ended = max(resolve_now_us(None), job.started_us or 0)
        store.dead_letter(
            job.job_id, DeadReason.ORPHANED_BY_RESTART, detail=detail, now_us=ended
        )
        ids.append(job.job_id)
    return ids


def _record_orphan(runs: RunStore, job: Job, record: dict, settings) -> str:
    parser = StreamParser()
    parser.session_id = job.session_id
    transcript = transcript_path(settings, job.session_id) if settings else None
    try:
        write = record_then_collect(
            runs,
            parser=parser,
            job=job,
            started_us=job.started_us or 0,
            ended_us=max(resolve_now_us(None), job.started_us or 0),
            failed=True,
            transcript=transcript,
            record=record,
        )
    except Exception as exc:  # reconcile must always finish
        return f"the run could not be recorded: {exc}"
    if write.record_error is not None:
        return f"the run row could not be written: {write.record_error}"
    return (
        "a FAILED run row was written, timed from the admit time "
        "because no spawn time survives a restart"
        if write.recorded
        else "no run row was written (no session id, or already recorded)"
    )


def pending_jobs(store: JobStore) -> list[Job]:
    """PENDING jobs, grouped by project, oldest first within a project."""
    jobs = store.list_by_state(JobState.PENDING)
    first_seen: dict[str, int] = {}
    for job in jobs:
        first_seen.setdefault(job.repo, len(first_seen))
    return sorted(jobs, key=lambda job: first_seen[job.repo])
