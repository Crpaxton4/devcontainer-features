"""``dispatch``: the dispatcher entry point.

::

    dispatch run '<JSON array>'   submit the approved set, then admit and run
                                  until nothing is pending
    dispatch run                  no argument: resume whatever is PENDING
    dispatch dead-letters         print the dead letter list as JSON (read only)

The JSON array is **one argument**; each element is
``{"repo": "...", "task": "...", "brief": "<path readable as text>"}`` with an
optional ``"round": <int>``. ``run`` prints one JSON object on stdout
(``{"batch_id": ..., "jobs": [...]}``); everything else goes to stderr.

Exit codes: 0 the queue drained (dead letters are data, not failure), 1 a job
raised inside the dispatcher itself, 2 bad arguments or settings, 3 another
dispatcher holds the lock.

The singleton lock is taken first, before anything else, and no flag skips it.
Running two dispatchers means pointing the second at a second state root
(``ODOO_DISPATCH_STATE_DIR``). ``dead-letters`` only reads, so it takes no
lock and works while a dispatcher runs.
"""

import argparse
import json
import signal
import sys
from collections.abc import Sequence

from odoo_sdk.state.dispatch_state import lock_path
from odoo_sdk.state.jobs import Job, JobStore
from odoo_sdk.state.runs_store import RunStore

from .engine import Engine
from .gates import AdmissionGate
from .lifecycle import Shell
from .lock import AlreadyRunningError, DispatcherLock
from .queue import SubmissionError, new_id, parse_submission, reconcile, submit
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
    sub.add_parser("dead-letters", help="print the dead letter list as JSON")
    return parser


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
        engine.run()
        ids = [job.job_id for job in submitted] or sorted(engine.notes)
        report = {
            "batch_id": batch_id if specs else "",
            "jobs": [job_report(store.get(i), engine.notes.get(i)) for i in ids],
        }
        print(json.dumps(report, indent=2))
        return EXIT_JOB_ERROR if engine.errors else EXIT_OK


def _dead_letters() -> int:
    store = JobStore()
    print(json.dumps([job_report(job) for job in store.dead_letters()], indent=2))
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "dead-letters":
        return _dead_letters()
    try:
        return _run(args.jobs)
    except AlreadyRunningError as exc:
        print(f"dispatch: {exc}", file=sys.stderr)
        return EXIT_LOCKED


if __name__ == "__main__":
    sys.exit(main())
