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
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Optional

from odoo_sdk.adapters import UnknownEventSourceError, source_to_event_type
from odoo_sdk.client import OdooClient
from odoo_sdk.sessionization import EventType
from odoo_sdk.state import EventRecord, ProjectIdError
from odoo_sdk.state import LocalStateClient as TaskStateDB
from odoo_sdk.state import TaskState
from odoo_sdk.utilities.env import (
    OdooDevcontainerRequiredError,
    assert_odoo_devcontainer,
)
from odoo_sdk.utilities.odoo_helpers import merge_timesheets, update_timesheet

# Subcommands that operate purely on local state: they need no Odoo devcontainer
# and construct no OdooClient. Claude Code hooks call these from arbitrary cwds.
_LOCAL_ONLY = {"log-event"}


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
            task_ids=[str(task_id) for task_id in args.task_id],
            repo="",
            subject=args.subject,
            payload=payload,
        )
    )
    print(f"Logged event {args.source!r} ({event_type.name}).")


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
        "--payload", default=None, help="Optional JSON object string of extra fields"
    )
    log_p.add_argument(
        "--timestamp",
        type=_parse_timestamp,
        default=None,
        help="ISO-8601 event time (default: now, UTC)",
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
    else:
        parser.print_help()
        sys.exit(1)


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.command is None:
        args.command = "list"
    if args.command in _LOCAL_ONLY:
        cmd_log_event(args)
        return
    _assert_env()
    _dispatch(parser, TaskStateDB(), args)


if __name__ == "__main__":
    main()
