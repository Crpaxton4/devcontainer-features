"""CLI companion for Odoo task time-tracking.

Usage:
    python -m odoo_sdk.cli [subcommand] [args]

Subcommands:
    list                    Show all active sessions (default)
    stop <session_id>       Force-stop one active session
    stop-all                Force-stop all active sessions
    report [--all]          Formatted table of sessions
    normalize [--apply]     Detect and merge duplicate timesheet entries
"""

import argparse
import sys
from datetime import date

from odoo_sdk.client import OdooClient
from odoo_sdk.task_tracker.env_check import (
    OdooDevcontainerRequiredError,
    assert_odoo_devcontainer,
)
from odoo_sdk.task_tracker.odoo_ops import merge_timesheets, update_timesheet
from odoo_sdk.task_tracker.state import TaskStateDB


def _assert_env() -> None:
    try:
        assert_odoo_devcontainer()
    except OdooDevcontainerRequiredError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _fmt_row(session, *, include_state: bool = True) -> str:
    parts = [
        f"[{session.id}]",
        f"{session.task_name[:40]:<40}",
        f"{session.project_name[:25]:<25}",
    ]
    if include_state:
        parts.append(f"{session.state.value:<18}")
    parts.append(session.elapsed_human)
    return "  ".join(parts)


def cmd_list(db: TaskStateDB, _args: argparse.Namespace) -> None:
    sessions = db.get_all_active_sessions()
    if not sessions:
        print("No active sessions.")
        return
    header = f"{'[ID]':<5}  {'Task':<40}  {'Project':<25}  {'State':<18}  Elapsed"
    print(header)
    print("-" * len(header))
    for s in sessions:
        print(_fmt_row(s))


def cmd_stop(db: TaskStateDB, args: argparse.Namespace, client: OdooClient) -> None:
    session_id: int = args.session_id
    session = db.get_session_by_id(session_id)
    if session is None:
        print(f"Error: no session with id {session_id}.", file=sys.stderr)
        sys.exit(1)
    from odoo_sdk.task_tracker.state import TaskState
    if session.state == TaskState.STOPPED:
        print(f"Session {session_id} is already stopped.")
        return

    description = "[/] Session ended via CLI force-stop"
    if session.timesheet_id is not None:
        update_timesheet(client, session.timesheet_id, session.elapsed_hours, description)

    stopped = db.stop_session(session.task_id)
    print(
        f"Stopped session {session_id}: {stopped.task_name!r}  "
        f"elapsed={stopped.elapsed_human}"
    )


def cmd_stop_all(db: TaskStateDB, _args: argparse.Namespace, client: OdooClient) -> None:
    sessions = db.get_all_active_sessions()
    if not sessions:
        print("Nothing to stop.")
        return
    description = "[/] Session ended via CLI force-stop"
    for s in sessions:
        if s.timesheet_id is not None:
            update_timesheet(client, s.timesheet_id, s.elapsed_hours, description)
        db.stop_session(s.task_id)
        print(f"Stopped session {s.id}: {s.task_name!r}  elapsed={s.elapsed_human}")


def cmd_report(db: TaskStateDB, args: argparse.Namespace) -> None:
    sessions = db.get_all_sessions() if args.all else db.get_all_active_sessions()
    if not sessions:
        print("No sessions found.")
        return
    header = f"{'[ID]':<5}  {'Task':<40}  {'Project':<25}  {'State':<18}  Elapsed"
    print(header)
    print("-" * len(header))
    for s in sessions:
        print(_fmt_row(s))


def cmd_normalize(db: TaskStateDB, args: argparse.Namespace, client: OdooClient) -> None:
    """Detect and optionally merge duplicate timesheet entries for same task+date."""
    stopped = db.get_stopped_sessions_with_timesheet()

    # Group by (task_id, calendar_date)
    groups: dict[tuple[int, str], list] = {}
    for s in stopped:
        day = s.started_at.date().isoformat()
        key = (s.task_id, day)
        groups.setdefault(key, []).append(s)

    duplicates = {k: v for k, v in groups.items() if len(v) > 1}

    if not duplicates:
        print("No duplicate timesheet entries found.")
        return

    for (task_id, day), sessions in duplicates.items():
        task_name = sessions[0].task_name
        total_hours = sum(s.elapsed_hours for s in sessions)
        ids = [s.timesheet_id for s in sessions if s.timesheet_id]
        print(
            f"Task {task_id!r} ({task_name}) on {day}: "
            f"{len(ids)} entries totalling {total_hours:.2f}h  "
            f"timesheet_ids={ids}"
        )
        if args.apply and len(ids) > 1:
            primary = min(ids)
            others = [i for i in ids if i != primary]
            merge_timesheets(client, primary, others)
            for s in sessions:
                if s.timesheet_id in others:
                    db.remap_timesheet_id(s.timesheet_id, primary)
            print(f"  -> Merged into timesheet {primary}.")

    if not args.apply:
        print("\nDry run — pass --apply to execute the merge.")


def main() -> None:
    _assert_env()

    parser = argparse.ArgumentParser(
        prog="python -m odoo_sdk.cli",
        description="Odoo task time-tracking companion CLI",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("list", help="Show active sessions (default)")

    stop_p = subparsers.add_parser("stop", help="Force-stop one session")
    stop_p.add_argument("session_id", type=int, help="SQLite session id from 'list'")

    subparsers.add_parser("stop-all", help="Force-stop all active sessions")

    report_p = subparsers.add_parser("report", help="Formatted session table")
    report_p.add_argument("--all", action="store_true", help="Include STOPPED sessions")

    normalize_p = subparsers.add_parser("normalize", help="Merge duplicate timesheets")
    normalize_p.add_argument(
        "--apply", action="store_true", help="Execute merge (default: dry run)"
    )

    args = parser.parse_args()
    if args.command is None:
        args.command = "list"

    db = TaskStateDB()

    if args.command == "list":
        cmd_list(db, args)
    elif args.command == "stop":
        client = OdooClient()
        cmd_stop(db, args, client)
    elif args.command == "stop-all":
        client = OdooClient()
        cmd_stop_all(db, args, client)
    elif args.command == "report":
        cmd_report(db, args)
    elif args.command == "normalize":
        client = OdooClient()
        cmd_normalize(db, args, client)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
