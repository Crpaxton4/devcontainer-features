"""CLI companion for Odoo task time-tracking.

Usage:
    python -m odoo_sdk.cli [subcommand] [args]

Subcommands:
    list                    Show all active runs (default)
    stop <run_id>           Force-stop one active run
    stop-all                Force-stop all active runs
    report [--all]          Formatted table of runs
    normalize [--apply]     Detect and merge duplicate timesheet entries
    log-event               Record a Claude Code hook event into local state
    discover                List all tracker projects and their active runs
    abort <hash> <run_id>   Abort a stale run in another project's DB
    resync [--sources ...]  Reconcile local events against git/GitHub/Odoo chatter
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Optional

from odoo_sdk.adapters import (
    UnknownEventSourceError,
    source_to_event_type,
    sync_git_log,
    sync_github,
    sync_odoo_chatter,
)
from odoo_sdk.client import OdooClient
from odoo_sdk.commands.builtin.abort_run import AbortRunCommand
from odoo_sdk.sessionization import EventType
from odoo_sdk.state import EventRecord, ProjectIdError
from odoo_sdk.state import LocalStateClient as TaskStateDB
from odoo_sdk.state import TaskState
from odoo_sdk.state.discovery import discover_projects
from odoo_sdk.utilities.env import (
    OdooDevcontainerRequiredError,
    assert_odoo_devcontainer,
)
from odoo_sdk.utilities.odoo_helpers import merge_timesheets, update_timesheet

# Subcommands that skip the global Odoo devcontainer assert and build no
# OdooClient up front. ``log-event`` / ``discover`` are purely local; ``resync``
# is local-first — its git/github pullers never touch Odoo, and it constructs an
# OdooClient lazily (behind its own env assert) only when ``odoo`` is requested,
# so the global assert must not gate it.
_LOCAL_ONLY = {"log-event", "discover", "resync"}


def _assert_env() -> None:
    try:
        assert_odoo_devcontainer()
    except OdooDevcontainerRequiredError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _fmt_row(run, *, include_state: bool = True) -> str:
    parts = [
        f"[{run.id}]",
        f"{run.task_name[:40]:<40}",
        f"{run.project_name[:25]:<25}",
    ]
    if include_state:
        parts.append(f"{run.state.value:<18}")
    parts.append(run.elapsed_human)
    return "  ".join(parts)


def cmd_list(db: TaskStateDB, _args: argparse.Namespace) -> None:
    runs = db.get_all_active_runs()
    if not runs:
        print("No active runs.")
        return
    header = f"{'[ID]':<5}  {'Task':<40}  {'Project':<25}  {'State':<18}  Elapsed"
    print(header)
    print("-" * len(header))
    for r in runs:
        print(_fmt_row(r))


def cmd_stop(db: TaskStateDB, args: argparse.Namespace, client: OdooClient) -> None:
    run_id: int = args.run_id
    run = db.get_run_by_id(run_id)
    if run is None:
        print(f"Error: no run with id {run_id}.", file=sys.stderr)
        sys.exit(1)
    if run.state == TaskState.STOPPED:
        print(f"Run {run_id} is already stopped.")
        return

    description = "[/] Run ended via CLI force-stop"
    if run.timesheet_id is not None:
        update_timesheet(client, run.timesheet_id, run.elapsed_hours, description)

    stopped = db.stop_run(run.task_id)
    print(
        f"Stopped run {run_id}: {stopped.task_name!r}  "
        f"elapsed={stopped.elapsed_human}"
    )


def cmd_stop_all(db: TaskStateDB, _args: argparse.Namespace, client: OdooClient) -> None:
    runs = db.get_all_active_runs()
    if not runs:
        print("Nothing to stop.")
        return
    description = "[/] Run ended via CLI force-stop"
    for r in runs:
        if r.timesheet_id is not None:
            update_timesheet(client, r.timesheet_id, r.elapsed_hours, description)
        db.stop_run(r.task_id)
        print(f"Stopped run {r.id}: {r.task_name!r}  elapsed={r.elapsed_human}")


def cmd_report(db: TaskStateDB, args: argparse.Namespace) -> None:
    runs = db.get_all_runs() if args.all else db.get_all_active_runs()
    if not runs:
        print("No runs found.")
        return
    header = f"{'[ID]':<5}  {'Task':<40}  {'Project':<25}  {'State':<18}  Elapsed"
    print(header)
    print("-" * len(header))
    for r in runs:
        print(_fmt_row(r))


def _find_duplicate_timesheets(stopped: list) -> dict[tuple[int, str], list]:
    """Group stopped runs by task and calendar date, keeping only duplicates.

    :param stopped: Stopped runs that each carry a timesheet id.
    :type stopped: list
    :returns: Mapping of ``(task_id, day)`` to the runs sharing that key,
        limited to keys with more than one run.
    :rtype: dict[tuple[int, str], list]
    """
    groups: dict[tuple[int, str], list] = {}
    for r in stopped:
        day = r.started_at.date().isoformat()
        key = (r.task_id, day)
        groups.setdefault(key, []).append(r)
    return {k: v for k, v in groups.items() if len(v) > 1}


def _apply_timesheet_merge(
    db: TaskStateDB,
    client: OdooClient,
    runs: list,
    ids: list[int],
) -> int:
    """Merge duplicate timesheet entries into the lowest id and remap runs.

    :param db: Local task-state database used to remap timesheet ids.
    :type db: TaskStateDB
    :param client: Odoo client used to merge the remote timesheets.
    :type client: OdooClient
    :param runs: Runs sharing the same task and calendar date.
    :type runs: list
    :param ids: Timesheet ids belonging to ``runs``.
    :type ids: list[int]
    :returns: The primary timesheet id the others were merged into.
    :rtype: int
    """
    primary = min(ids)
    others = [i for i in ids if i != primary]
    merge_timesheets(client, primary, others)
    for r in runs:
        if r.timesheet_id in others:
            db.remap_timesheet_id(r.timesheet_id, primary)
    return primary


def cmd_normalize(db: TaskStateDB, args: argparse.Namespace, client: OdooClient) -> None:
    """Detect and optionally merge duplicate timesheet entries for same task+date."""
    duplicates = _find_duplicate_timesheets(db.get_stopped_runs_with_timesheet())

    if not duplicates:
        print("No duplicate timesheet entries found.")
        return

    for (task_id, day), runs in duplicates.items():
        task_name = runs[0].task_name
        total_hours = sum(r.elapsed_hours for r in runs)
        ids = [r.timesheet_id for r in runs if r.timesheet_id]
        print(
            f"Task {task_id!r} ({task_name}) on {day}: "
            f"{len(ids)} entries totalling {total_hours:.2f}h  "
            f"timesheet_ids={ids}"
        )
        if args.apply and len(ids) > 1:
            primary = _apply_timesheet_merge(db, client, runs, ids)
            print(f"  -> Merged into timesheet {primary}.")

    if not args.apply:
        print("\nDry run — pass --apply to execute the merge.")


def _parse_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 ``--timestamp`` value (argparse type function).

    :raises argparse.ArgumentTypeError: When ``value`` is not ISO-8601, so
        argparse reports the error and exits with status 2.
    """
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid ISO-8601 timestamp: {value!r}"
        ) from exc


def _resolve_source(source: str) -> EventType:
    """Validate ``--source`` via the strict resolver; exit 2 if unknown."""
    try:
        return source_to_event_type(source)
    except UnknownEventSourceError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)


def _parse_payload(raw: Optional[str]) -> Optional[dict]:
    """Parse the optional ``--payload`` JSON object; exit 2 if malformed."""
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"Error: invalid --payload JSON: {exc}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(parsed, dict):
        print("Error: --payload must be a JSON object.", file=sys.stderr)
        sys.exit(2)
    return parsed


def _open_local_db() -> TaskStateDB:
    """Open the cwd-resolved local state DB, or exit 0 when not a git repo.

    Hooks run in arbitrary working directories; a non-git cwd is expected, not
    an error. A nonzero exit would degrade the Claude Code session, so we emit a
    one-line notice and exit 0 without writing an event.
    """
    try:
        return TaskStateDB()
    except ProjectIdError as exc:
        print(f"Notice: {exc} Skipping event log.", file=sys.stderr)
        sys.exit(0)


def _resolve_task_ids(db: TaskStateDB, args: argparse.Namespace) -> list[str]:
    """Resolve which task ids a logged event attributes to.

    An explicit ``--task-id`` (repeatable) always wins. Otherwise, when
    ``--attach-active-run`` is passed, attach every active (RUNNING /
    AWAITING_ANSWERS) run in the cwd-resolved project DB — the natural
    association for a hook firing while an FSM run is in progress. With no
    active run (or the flag absent) this yields ``[]``, i.e. untargeted
    session-level activity, matching the pre-flag default.
    """
    if args.task_id:
        return [str(task_id) for task_id in args.task_id]
    if args.attach_active_run:
        return [str(run.task_id) for run in db.get_all_active_runs()]
    return []


def cmd_log_event(args: argparse.Namespace) -> None:
    """Record a single Claude Code hook event into the local ``events`` table."""
    event_type = _resolve_source(args.source)
    payload = _parse_payload(args.payload)
    timestamp = args.timestamp or datetime.now(timezone.utc)
    db = _open_local_db()
    db.add_event(
        EventRecord(
            id=None,
            source=args.source,
            timestamp=timestamp,
            task_ids=_resolve_task_ids(db, args),
            repo="",
            subject=args.subject,
            payload=payload,
        )
    )
    print(f"Logged event {args.source!r} ({event_type.name}).")


def _discover_run_line(project: dict, run: dict) -> str:
    """Format one active run row for the ``discover`` table."""
    flag = "STALE" if run["stale"] else ""
    return "  ".join(
        [
            f"{project['project_hash']:<16}",
            f"{project['repo_label'][:25]:<25}",
            f"[{run['run_id']}]",
            f"{run['task_name'][:30]:<30}",
            f"{run['state']:<18}",
            f"{run['started_at'][:19]:<19}",
            f"{flag:<5}",
        ]
    )


def cmd_discover(args: argparse.Namespace) -> None:
    """List every tracker project and its active runs across all local DBs."""
    projects = discover_projects(stale_after_hours=args.stale_after_hours)
    if not projects:
        print("No task-tracker projects found.")
        return
    header = (
        f"{'Project Hash':<16}  {'Repo':<25}  {'[ID]':<5}  {'Task':<30}  "
        f"{'State':<18}  {'Started':<19}  Flag"
    )
    print(header)
    print("-" * len(header))
    for project in projects:
        if project.get("note"):
            print(f"{project['project_hash']:<16}  {project['note']}")
            continue
        if not project["active_runs"]:
            print(
                f"{project['project_hash']:<16}  {project['repo_label'][:25]:<25}  "
                "(no active runs)"
            )
            continue
        for run in project["active_runs"]:
            print(_discover_run_line(project, run))


_RESYNC_SOURCES = ("git", "github", "odoo")


def _parse_resync_sources(raw: str) -> list[str]:
    """Return the requested resync pullers in stable order, ignoring unknowns."""
    requested = {token.strip() for token in raw.split(",") if token.strip()}
    return [source for source in _RESYNC_SOURCES if source in requested]


def _resync_odoo(db: TaskStateDB) -> dict:
    """Run the Odoo chatter puller, or skip cleanly when the env assert fails.

    The git/github pullers never need Odoo, so a resync that also requests
    ``odoo`` outside a configured devcontainer must degrade to a skip notice
    rather than aborting the whole command. The :class:`OdooClient` is built only
    after the assert passes, honoring the lazy-client contract.
    """
    try:
        assert_odoo_devcontainer()
    except OdooDevcontainerRequiredError:
        return {"skipped": "odoo devcontainer not configured"}
    return sync_odoo_chatter(OdooClient(), db)


def _format_resync_line(source: str, result: dict) -> str:
    """Format one per-source resync result line for the CLI."""
    if "skipped" in result:
        return f"{source}: skipped ({result['skipped']})"
    return f"{source}: inserted {result['inserted']}"


def cmd_resync(args: argparse.Namespace) -> None:
    """Reconcile local event state against git/GitHub/Odoo for the current repo.

    Local-first: the git and github pullers read only the local repo/CLI, while
    the odoo puller is guarded and constructed lazily. Each puller is idempotent
    and prints a per-source inserted count or skip reason.
    """
    sources = _parse_resync_sources(args.sources)
    db = _open_local_db()
    runners = {"git": sync_git_log, "github": sync_github, "odoo": _resync_odoo}
    for source in sources:
        result = runners[source](db)
        print(_format_resync_line(source, result))


def cmd_abort(args: argparse.Namespace, client: OdooClient) -> None:
    """Abort a stale run in another project's DB and close its Odoo anchor."""
    result = AbortRunCommand(client).execute(args.project_hash, args.run_id)
    if result["already_stopped"]:
        print(
            f"Run {result['run_id']} in {result['project_hash']} is already "
            "stopped; nothing to abort."
        )
        return
    anchor = "closed" if result["anchor_closed"] else "left untouched"
    print(
        f"Aborted run {result['run_id']} ({result['task_name']!r}) in "
        f"{result['project_hash']}; anchor {anchor}."
    )


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all subcommands declared.

    :returns: Configured parser for the time-tracking companion CLI.
    :rtype: argparse.ArgumentParser
    """
    parser = argparse.ArgumentParser(
        prog="python -m odoo_sdk.cli",
        description="Odoo task time-tracking companion CLI",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("list", help="Show active runs (default)")

    stop_p = subparsers.add_parser("stop", help="Force-stop one run")
    stop_p.add_argument("run_id", type=int, help="SQLite run id from 'list'")

    subparsers.add_parser("stop-all", help="Force-stop all active runs")

    report_p = subparsers.add_parser("report", help="Formatted run table")
    report_p.add_argument("--all", action="store_true", help="Include STOPPED runs")

    normalize_p = subparsers.add_parser("normalize", help="Merge duplicate timesheets")
    normalize_p.add_argument(
        "--apply", action="store_true", help="Execute merge (default: dry run)"
    )

    log_p = subparsers.add_parser(
        "log-event", help="Record a Claude Code hook event into local state"
    )
    log_p.add_argument(
        "--source", required=True, help="Event source, e.g. claude:SessionStart"
    )
    log_p.add_argument("--subject", default="", help="Human-readable event subject")
    log_p.add_argument(
        "--task-id",
        action="append",
        type=int,
        default=[],
        dest="task_id",
        help="Task id to attribute the event to (repeatable)",
    )
    log_p.add_argument(
        "--attach-active-run",
        action="store_true",
        dest="attach_active_run",
        help="Attach the event to any active run's task id when no explicit "
        "--task-id is given (default: leave untargeted)",
    )
    log_p.add_argument(
        "--payload", default=None, help="Optional JSON object string of extra fields"
    )
    log_p.add_argument(
        "--timestamp",
        type=_parse_timestamp,
        default=None,
        help="ISO-8601 event time (default: now, UTC)",
    )

    discover_p = subparsers.add_parser(
        "discover", help="List all tracker projects and their active runs"
    )
    discover_p.add_argument(
        "--stale-after-hours",
        type=float,
        default=12.0,
        dest="stale_after_hours",
        help="Flag active runs started before this many hours ago (default: 12)",
    )

    abort_p = subparsers.add_parser(
        "abort", help="Abort a stale run in another project's DB"
    )
    abort_p.add_argument("project_hash", help="Project hash from 'discover'")
    abort_p.add_argument("run_id", type=int, help="Run id (or task id) to abort")

    resync_p = subparsers.add_parser(
        "resync", help="Reconcile local events against git/GitHub/Odoo chatter"
    )
    resync_p.add_argument(
        "--sources",
        default="git,github,odoo",
        help="Comma-separated subset of git,github,odoo (default: all three)",
    )
    return parser


def _dispatch(
    parser: argparse.ArgumentParser,
    db: TaskStateDB,
    args: argparse.Namespace,
) -> None:
    """Route parsed arguments to the matching command handler.

    :param parser: Parser used to print help for unknown commands.
    :type parser: argparse.ArgumentParser
    :param db: Local task-state database.
    :type db: TaskStateDB
    :param args: Parsed CLI arguments.
    :type args: argparse.Namespace
    """
    if args.command == "list":
        cmd_list(db, args)
    elif args.command == "stop":
        cmd_stop(db, args, OdooClient())
    elif args.command == "stop-all":
        cmd_stop_all(db, args, OdooClient())
    elif args.command == "report":
        cmd_report(db, args)
    elif args.command == "normalize":
        cmd_normalize(db, args, OdooClient())
    elif args.command == "abort":
        cmd_abort(args, OdooClient())
    else:
        parser.print_help()
        sys.exit(1)


def _dispatch_local_only(args: argparse.Namespace) -> None:
    """Route a local-only command that constructs no OdooClient.

    These subcommands read (and, for ``log-event``, write) only local tracker
    state, so they skip the devcontainer assert and never build an OdooClient.
    """
    if args.command == "discover":
        cmd_discover(args)
    elif args.command == "resync":
        cmd_resync(args)
    else:
        cmd_log_event(args)


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.command is None:
        args.command = "list"
    if args.command in _LOCAL_ONLY:
        _dispatch_local_only(args)
        return
    _assert_env()
    _dispatch(parser, TaskStateDB(), args)


if __name__ == "__main__":
    main()
