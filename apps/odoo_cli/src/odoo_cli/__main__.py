"""CLI companion for Odoo task time-tracking: argparse wiring and dispatch only.

Usage:
    python -m odoo_cli [subcommand] [args]

Subcommands:
    normalize [--apply]     Detect and merge duplicate timesheet entries
    log-event               Record a Claude Code hook event into local state
    get-employee-id         Print the hr.employee id for the current user
    resync [--sources ...]  Reconcile local events against git/GitHub/Odoo chatter
    upload [--start ...]    Bill derived sessions to Odoo (headless TUI upload)
    cmd <name> [--args J]   Dispatch any registry command; stdout is one JSON doc
    doctor [--migrate]      Report on (or migrate) the ODOO_DEV_HOME state root
    repo-map <verb>         Read and edit the project-to-repo map

This module imports neither the logic layer nor the data layer. Each handler
module is imported inside ``main`` once the arguments are parsed, so ``--version``,
``doctor`` and ``repo-map`` stay fast.
"""

import argparse
import importlib
import sys
from datetime import date, datetime
from typing import Optional, Sequence

# Subcommands that run without a command service: command -> (module, handler).
_STANDALONE = {
    "doctor": ("odoo_cli.doctor", "run_doctor"),
    "repo-map": ("odoo_cli.repo_map", "run_repo_map"),
}


def _parse_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 ``--timestamp`` value (argparse type function)."""
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid ISO-8601 timestamp: {value!r}"
        ) from exc


def _parse_iso_date(value: str) -> str:
    """Validate an ISO ``YYYY-MM-DD`` argparse value, returning it unchanged."""
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO date: {value!r}") from exc
    return value


def _add_json(parser: argparse.ArgumentParser, help_text: str = "print JSON") -> None:
    parser.add_argument("--json", action="store_true", dest="as_json", help=help_text)


def _add_log_event(subparsers) -> None:
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
        help="Attribute the event to the --branch task id when no explicit "
        "--task-id is given (default: leave untargeted)",
    )
    log_p.add_argument(
        "--branch",
        default=None,
        help="Session branch (e.g. '<task-id>-<slug>'). Recorded as the event's "
        "provenance and, via the '<task-id>-' convention, used to recover the "
        "task attribution when no --task-id names it (#574). "
        "Default: resolve from the cwd checkout for provenance only.",
    )
    log_p.add_argument(
        "--repo",
        default=None,
        help="Repository the event originated from, as either a raw git remote "
        "URL or an already-normalized 'owner/repo' label; a URL is normalized "
        "to 'owner/repo'. State it when the event's repo is not this process's "
        "cwd (the hook shim reads the session's cwd). Default/blank: resolve "
        "from the cwd's origin remote.",
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


def _add_window(parser: argparse.ArgumentParser, what: str) -> None:
    for flag, label in (("--start", "start"), ("--end", "end")):
        parser.add_argument(
            flag,
            type=_parse_iso_date,
            default=None,
            help=f"Inclusive ISO {label} date (YYYY-MM-DD){what}",
        )


def _add_service_commands(subparsers) -> None:
    normalize_p = subparsers.add_parser("normalize", help="Merge duplicate timesheets")
    normalize_p.add_argument(
        "--apply", action="store_true", help="Execute merge (default: dry run)"
    )
    _add_log_event(subparsers)
    subparsers.add_parser(
        "get-employee-id", help="Print the hr.employee id for the current user"
    )
    resync_p = subparsers.add_parser(
        "resync", help="Reconcile local events against git/GitHub/Odoo chatter"
    )
    resync_p.add_argument(
        "--sources",
        default="git,github,odoo",
        help="Comma-separated subset of git,github,odoo (default: git,github,odoo)",
    )
    _add_window(resync_p, " for the capture window")
    upload_p = subparsers.add_parser(
        "upload", help="Bill derived sessions to Odoo (headless TUI upload path)"
    )
    _add_window(upload_p, "")
    upload_p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Preview the billable sessions without writing to Odoo",
    )
    cmd_p = subparsers.add_parser(
        "cmd",
        help="Dispatch any registry command by name; stdout is one JSON document",
    )
    cmd_p.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Registry command name to dispatch (see 'cmd --list')",
    )
    cmd_p.add_argument(
        "--args",
        default=None,
        dest="cmd_args",
        help="JSON object of keyword arguments passed to the command's execute()",
    )
    cmd_p.add_argument(
        "--list",
        action="store_true",
        dest="list_commands",
        help="List every registry command as {name, description}",
    )
    _add_json(cmd_p, "With --list, print the listing as a JSON array")


def _add_doctor(subparsers) -> None:
    doctor_p = subparsers.add_parser(
        "doctor", help="Report on the ODOO_DEV_HOME state root"
    )
    doctor_p.add_argument(
        "--migrate", action="store_true", help="Import every legacy store"
    )
    doctor_p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="With --migrate: change nothing",
    )
    _add_json(doctor_p)


def _add_repo_map(subparsers) -> None:
    repo_p = subparsers.add_parser("repo-map", help="Read and edit the repo map")
    verbs = repo_p.add_subparsers(dest="verb", required=True)
    _add_json(verbs.add_parser("list", help="List the projects"))
    get_p = verbs.add_parser("get", help="Show one project")
    get_p.add_argument("name")
    _add_json(get_p)
    resolve_p = verbs.add_parser("resolve", help="Resolve a project and its checkout")
    resolve_p.add_argument("query", nargs="?", default=None)
    resolve_p.add_argument("--repos-dir", default=None, help="Folder of checkouts")
    _add_json(resolve_p)
    set_p = verbs.add_parser("set", help="Create or replace a project")
    set_p.add_argument("name")
    set_p.add_argument("--repo", required=True, help="Folder name or absolute path")
    set_p.add_argument("--odoo-version", required=True, help="Series, e.g. 17.0")
    set_p.add_argument("--aliases", default="", help="Comma-separated aliases")
    set_p.add_argument("--branch-flow", default="", help="Comma-separated branches")
    set_p.add_argument(
        "--upgrade-branch",
        action="append",
        default=[],
        metavar="SERIES=BRANCH",
        help="Upgrade base branch of one series (repeatable)",
    )
    set_p.add_argument("--release-assignee", default="")
    set_p.add_argument("--release-reviewer", default="")
    _add_json(set_p)
    remove_p = verbs.add_parser("remove", help="Remove a project")
    remove_p.add_argument("name")
    _add_json(remove_p)
    _add_json(verbs.add_parser("validate", help="Check the stored map"))
    defaults_p = verbs.add_parser("defaults", help="Show or set the defaults")
    defaults_p.add_argument("--branch-flow", default=None)
    defaults_p.add_argument("--release-assignee", default=None)
    defaults_p.add_argument("--release-reviewer", default=None)
    _add_json(defaults_p)


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all subcommands declared."""
    parser = argparse.ArgumentParser(
        prog="odoo-dev", description="Odoo task time-tracking companion CLI"
    )
    parser.add_argument(
        "--version", action="store_true", help="Print the version and exit"
    )
    subparsers = parser.add_subparsers(dest="command")
    _add_service_commands(subparsers)
    _add_doctor(subparsers)
    _add_repo_map(subparsers)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse ``argv``, import the handler it names and return its exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.version:
        from importlib.metadata import version

        print(f"odoo-dev {version('odoo_cli')}")
        return 0
    if args.command is None:
        parser.print_help()
        return 1
    if args.command in _STANDALONE:
        module, handler = _STANDALONE[args.command]
        return getattr(importlib.import_module(module), handler)(args)
    from .commands import dispatch

    return dispatch(args)


if __name__ == "__main__":
    sys.exit(main())
