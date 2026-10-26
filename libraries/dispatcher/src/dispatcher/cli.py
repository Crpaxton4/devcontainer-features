"""``dispatch``: the dispatcher entry point.

::

    dispatch run '<JSON array>'   submit the approved set, then admit and run
                                  until nothing is pending
    dispatch run                  no argument: resume whatever is PENDING
    dispatch submit '<JSON array>'
                                  write the PENDING rows and exit; no lock, so
                                  it works while a dispatcher runs
    dispatch report <batch_id>    print one batch as JSON (read only, no lock)
    dispatch dead-letters         print the dead letter list as JSON (read only)

The JSON array is **one argument**; each element is
``{"repo": "...", "task": "...", "brief": "<path readable as text>"}`` with an
optional ``"round": <int>``. ``run`` prints one JSON object on stdout
(``{"batch_id": ..., "jobs": [...]}``), and so do ``submit`` and ``report``;
everything else goes to stderr.

``submit`` is all or nothing: if any task already has a PENDING or RUNNING job,
or the array names a task twice, it exits 2 and writes nothing (``run`` instead
records a DUPLICATE dead letter). It also refuses, with exit 2, a repo that has no
checkout or no central config. A running dispatcher admits the new rows at
its next step. ``report`` adds ``bundle`` (the artifact directory) to a job whose
directory exists. It cannot give ``reap``, ``reaped``, ``missing`` or ``notes``,
which only the run process holds. An unknown batch id prints
``{"batch_id": "<id>", "jobs": []}`` and exits 0.

Exit codes: 0 the queue drained (dead letters are data, not failure), 1 a job
raised inside the dispatcher itself, the run stopped fatally, or any job is
still PENDING when it ends (a stop request, an unreadable memory figure), 2 bad
arguments or settings, 3 another dispatcher holds the lock. Exit 0 therefore
means nothing is left PENDING.

The singleton lock is taken first, before anything else, and no flag skips it.
Running two dispatchers means pointing the second at a second state root
(``ODOO_DISPATCH_STATE_DIR``). ``dead-letters`` and ``report`` only read and
``submit`` only inserts, so they take no lock and work while a dispatcher runs.
"""

import argparse
import json
import signal
import sys
from collections.abc import Sequence

from odoo_sdk.state.dispatch_state import bundle_dir, lock_path
from odoo_sdk.state.jobs import DuplicateTaskInFlightError, Job, JobState, JobStore
from odoo_sdk.state.runs_store import RunStore

from .engine import Engine
from .gates import AdmissionGate, MemoryReadError
from .lifecycle import Shell, StageError, require_workspace, resolve_config
from .lock import AlreadyRunningError, DispatcherLock
from .queue import (
    SubmissionError,
    new_id,
    order_by_project,
    parse_submission,
    reconcile,
    submit,
)
from .settings import Settings, SettingsError

EXIT_OK, EXIT_JOB_ERROR, EXIT_USAGE, EXIT_LOCKED = 0, 1, 2, 3


def job_report(job: Job, notes: dict | None = None) -> dict:
    """One job as the orchestrator reads it."""
    report = {
        "job_id": job.job_id,
        "task": job.task_id,
        "repo": job.repo,
        "round": job.round,
        "state": job.state.value,
        "outcome": job.outcome,
        "dead_reason": job.dead_reason,
        "detail": job.detail,
        "session_id": job.session_id,
    }
    report.update(notes or {})
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dispatch", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="submit a JSON array of jobs and run them")
    run.add_argument("jobs", nargs="?", help="JSON array of {repo, task, brief}")
    submit_cmd = sub.add_parser(
        "submit",
        help="queue a JSON array of jobs and exit; refuses a repo with no "
        "checkout or no central config",
    )
    submit_cmd.add_argument("jobs", help="JSON array of {repo, task, brief}")
    report_cmd = sub.add_parser("report", help="print one batch as JSON")
    report_cmd.add_argument("batch_id")
    sub.add_parser("dead-letters", help="print the dead letter list as JSON")
    return parser


def _run_engine(engine: Engine) -> bool:
    """Run the queue. True when it ended on a fatal error with jobs left PENDING."""
    try:
        engine.run()
    except MemoryReadError as exc:
        print(
            f"dispatch: stopping, the memory figure stayed unreadable: {exc}; "
            "unadmitted jobs remain PENDING, rerun `dispatch run` to resume",
            file=sys.stderr,
        )
        return True
    return False


def _run(jobs_argument: str | None) -> int:
    with DispatcherLock(lock_path()):  # first, before anything else
        try:
            specs = parse_submission(jobs_argument) if jobs_argument else []
            settings = Settings.from_env()
        except (SubmissionError, SettingsError) as exc:
            print(f"dispatch: {exc}", file=sys.stderr)
            return EXIT_USAGE
        store, runs = JobStore(), RunStore()
        shell = Shell()
        reconcile(store, runs, shell, settings)  # before anything is admitted
        batch_id = new_id()
        submitted = submit(store, specs, batch_id) if specs else []
        engine = Engine(
            settings,
            store,
            runs,
            shell,
            AdmissionGate(settings.min_available_gb, settings.settle_s),
        )
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_: engine.request_stop())
        fatal = _run_engine(engine)
        left = store.list_by_state(JobState.PENDING)
        # Submitted jobs, jobs that finished here (a resume submits none), and
        # whatever is still PENDING, so the report never hides what was left.
        ids = dict.fromkeys(
            [j.job_id for j in submitted]
            + sorted(engine.notes)
            + [j.job_id for j in left]
        )
        report = {
            "batch_id": batch_id if specs else "",
            "jobs": [job_report(store.get(i), engine.notes.get(i)) for i in ids],
        }
        print(json.dumps(report, indent=2))
        return EXIT_JOB_ERROR if engine.errors or fatal or left else EXIT_OK


def _check_repos(settings: Settings, specs: Sequence) -> list[str]:
    """One problem line per repo with no checkout or no central config."""
    problems = []
    for repo in dict.fromkeys(spec.repo for spec in specs):
        try:
            require_workspace(settings, repo)
            resolve_config(settings, repo)
        except StageError as exc:
            problems.append(f"repo {repo}: {exc.detail}")
    return problems


def _submit(jobs_argument: str) -> int:
    try:
        specs = order_by_project(parse_submission(jobs_argument))
        settings = Settings.from_env()
    except (SubmissionError, SettingsError) as exc:
        print(f"dispatch: {exc}", file=sys.stderr)
        return EXIT_USAGE
    problems = _check_repos(settings, specs)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return EXIT_USAGE
    batch_id = new_id()
    rows = [
        dict(
            job_id=new_id(),
            batch_id=batch_id,
            task_id=spec.task_id,
            repo=spec.repo,
            brief=spec.brief,
            round=spec.round,
        )
        for spec in specs
    ]
    try:
        jobs = JobStore().submit_all(rows)
    except DuplicateTaskInFlightError as exc:
        print(
            f"dispatch: task {exc} already has a PENDING or RUNNING job "
            "(or is named twice); nothing was written",
            file=sys.stderr,
        )
        return EXIT_USAGE
    print(json.dumps({"batch_id": batch_id, "jobs": _reports(jobs)}, indent=2))
    return EXIT_OK


def _reports(jobs: list[Job]) -> list[dict]:
    """``job_report`` plus ``bundle`` where the artifact directory exists."""
    reports = []
    for job in jobs:
        directory = bundle_dir(job.job_id)
        notes = {"bundle": str(directory)} if directory.is_dir() else None
        reports.append(job_report(job, notes))
    return reports


def _report(batch_id: str) -> int:
    jobs = JobStore().list_by_batch(batch_id)
    print(json.dumps({"batch_id": batch_id, "jobs": _reports(jobs)}, indent=2))
    return EXIT_OK


def _dead_letters() -> int:
    store = JobStore()
    print(json.dumps([job_report(job) for job in store.dead_letters()], indent=2))
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "dead-letters":
        return _dead_letters()
    if args.command == "submit":
        return _submit(args.jobs)
    if args.command == "report":
        return _report(args.batch_id)
    try:
        return _run(args.jobs)
    except AlreadyRunningError as exc:
        print(f"dispatch: {exc}", file=sys.stderr)
        return EXIT_LOCKED


if __name__ == "__main__":
    sys.exit(main())
