"""Subcommand handlers that run on a command service.

Each ``run_*`` handler takes the wired ``CommandService`` and the parsed
arguments, formats the result of one ``service.execute(...)`` and returns the
process exit code. ``dispatch`` builds the service, checks the Odoo
configuration where a command needs it, logs the usage and calls the handler.
This module loads the logic layer, so ``__main__`` imports it only after parsing.
"""

import argparse
import json
import sys
import time
from typing import Any, Callable, NoReturn, Optional

from odoo_dev_core import CommandService
from odoo_dev_core.attribution import normalize_repo_label
from odoo_dev_core.registry import CommandArgumentError
from odoo_dev_core.sessionization.persistence import (
    UnknownEventSourceError,
    source_to_event_type,
)
from odoo_dev_core.telemetry import (
    BOUNDARY_ERRORS,
    declare_usage,
    emit_tool_event,
    error_payload,
    is_dispatched_run,
    record_usage,
)
from odoo_dev_core.workflows.resync import format_resync_line

from .bootstrap import assert_configured, bootstrap


class CliExit(Exception):
    """Stop the running handler with a process exit code."""

    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


def _usage_error(message: str) -> NoReturn:
    print(f"Error: {message}", file=sys.stderr)
    raise CliExit(2)


def run_normalize(service: CommandService, args: argparse.Namespace) -> int:
    """Detect and optionally merge duplicate timesheet entries for same task+date."""
    report = service.execute("normalize_timesheets", apply=args.apply)
    if not report["groups"]:
        print("No duplicate timesheet entries found.")
        return 0
    for group in report["groups"]:
        print(
            f"Task {group['task_id']!r} on {group['day']}: "
            f"{group['count']} entries totalling {group['total_hours']:.2f}h  "
            f"timesheet_ids={group['timesheet_ids']}"
        )
        if group["merged_into"] is not None:
            print(f"  -> Merged into timesheet {group['merged_into']}.")
    if not report["applied"]:
        print("\nDry run — pass --apply to execute the merge.")
    return 0


def _parse_payload(raw: Optional[str]) -> Optional[dict]:
    """Parse the optional ``--payload`` JSON object; exit 2 if malformed."""
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        _usage_error(f"invalid --payload JSON: {exc}")
    if not isinstance(parsed, dict):
        _usage_error("--payload must be a JSON object.")
    return parsed


def run_log_event(service: CommandService, args: argparse.Namespace) -> int:
    """Record one Claude Code hook event through the ``log_event`` command.

    Only the interface-specific resolution stays here: ``--source`` validation,
    ``--payload`` parsing and the ``--repo`` label. The hook shim states
    ``--branch`` and ``--repo`` because it reads them from the session's ``cwd``,
    which is not this process's cwd.
    """
    try:
        event_type = source_to_event_type(args.source)
    except UnknownEventSourceError as exc:
        _usage_error(str(exc))
    service.execute(
        "log_event",
        source=args.source,
        subject=args.subject,
        payload=_parse_payload(args.payload),
        task_ids=args.task_id,
        repo=normalize_repo_label(args.repo, service.services.git),
        branch=args.branch,
        timestamp=args.timestamp,
        attach_active_run=args.attach_active_run,
    )
    print(f"Logged event {args.source!r} ({event_type.name}).")
    return 0


_RESYNC_SOURCES = ("git", "github", "odoo")


def _odoo_configured(service: CommandService) -> bool:
    """Whether the Odoo connection settings resolve (#642)."""
    try:
        assert_configured(service.services.config)
    except ValueError:
        return False
    return True


def run_resync(service: CommandService, args: argparse.Namespace) -> int:
    """Reconcile local event state against git/GitHub/Odoo, directory-agnostic.

    The git and github pullers never need Odoo, so an ``odoo`` source that is not
    configured degrades to a skip line. All lines print first, then any
    ``error`` result makes the exit code 1.
    """
    requested = {token.strip() for token in args.sources.split(",")}
    selected = [source for source in _RESYNC_SOURCES if source in requested]
    runnable = [s for s in selected if s != "odoo" or _odoo_configured(service)]
    summary: dict[str, dict] = {}
    if runnable:
        summary = service.execute(
            "resync", sources=",".join(runnable), start=args.start, end=args.end
        )
    if "odoo" in selected and "odoo" not in runnable:
        summary["odoo"] = {"skipped": "odoo sdk not configured"}
    failed = False
    for source, result in summary.items():
        print(format_resync_line(source, result))
        for label in result.get("unattributed_reviews", []):
            print(f"unattributed review: {label}", file=sys.stderr)
        failed = failed or "error" in result
    return 1 if failed else 0


def run_upload(service: CommandService, args: argparse.Namespace) -> int:
    """Bill derived sessions headlessly with the ``upload`` command (#354)."""
    result = service.execute(
        "upload", start_date=args.start, end_date=args.end, dry_run=args.dry_run
    )
    verb = "would bill" if args.dry_run else "billed"
    for row in result["rows"]:
        target = "(dry run)" if args.dry_run else f"-> timesheet {row['timesheet_id']}"
        print(
            f"  task {row['task_id']}  {row['session_key']}  "
            f"{row['hours']:.2f}h  {target}"
        )
    print(
        f"{verb} {result['uploaded']} session(s); "
        f"skipped {result['skipped']} (no task id); "
        f"retired {result['retired']} orphaned upload(s)."
    )
    return 0


def run_get_employee_id(service: CommandService, _args: argparse.Namespace) -> int:
    """Print the ``hr.employee`` id for the authenticated Odoo user (#499)."""
    print(f"employee_id={service.execute('get_employee_id')}")
    return 0


def _fail(exc: BaseException, code: int) -> NoReturn:
    """Print the shared error envelope as stdout's one JSON document; exit.

    Exit 1 marks a boundary (caller-actionable) failure, 2 a usage error.
    """
    print(json.dumps(error_payload(exc), default=str))
    raise CliExit(code)


def _print_listing(service: CommandService, as_json: bool) -> None:
    """Print every CLI command as a ``{name, description}`` row, sorted by name."""
    entries = [
        {"name": spec.id, "description": spec.description}
        for spec in service.registry.specs("cli")
    ]
    if as_json:
        print(json.dumps(entries, default=str))
        return
    width = max(len(entry["name"]) for entry in entries)
    header = f"{'Command':<{width}}  Description"
    print(header)
    print("-" * len(header))
    for entry in entries:
        summary = entry["description"].strip().splitlines()
        print(f"{entry['name']:<{width}}  {summary[0] if summary else ''}")


def _parse_kwargs(raw: Optional[str]) -> dict[str, Any]:
    """Parse ``--args`` into the command's kwargs; usage-fail (exit 2) if bad."""
    if raw is None:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        _fail(ValueError(f"malformed --args JSON: {exc}"), 2)
    if not isinstance(parsed, dict):
        _fail(ValueError("--args must be a JSON object"), 2)
    return parsed


def _emit_event(service: CommandService, name: str, kwargs: dict, result: Any) -> None:
    """Emit the one ``source="agent"`` event a successful dispatch owns.

    Best-effort: a failing state store never turns a success into an error. A
    dispatched run (``ODOO_UNATTENDED``) emits nothing (#1065).
    """
    if is_dispatched_run(service.services.env):
        return
    try:
        emit_tool_event(service, name, kwargs, result, via="cli")
    except Exception:
        pass


def _log_usage(name: str, started: float, ok: bool) -> None:
    """Write one ``sdk_command`` usage line (#1204): names and outcome only."""
    record_usage(
        "sdk_command",
        name,
        source="odoo_client",
        via="cli",
        ok=ok,
        ms=int((time.monotonic() - started) * 1000),
    )


def run_cmd(service: CommandService, args: argparse.Namespace) -> int:
    """Generic dispatcher: run any registered command, stdout one JSON document.

    ``cmd --list`` lists the CLI surface and ``cmd <name> --args '<json>'`` runs
    one command with the JSON object as kwargs. A boundary error prints the
    shared MCP error envelope and exits 1. A usage error (unknown name,
    malformed ``--args``, kwargs that do not fit) prints it and exits 2.
    """
    if args.list_commands:
        _print_listing(service, args.as_json)
        return 0
    if args.name is None:
        _fail(ValueError("a command name (or --list) is required"), 2)
    kwargs = _parse_kwargs(args.cmd_args)
    try:
        service.registry.get(args.name)
    except KeyError:
        _fail(ValueError(f"unknown command: {args.name!r} (see 'cmd --list')"), 2)
    started = time.monotonic()
    try:
        result = service.execute(args.name, **kwargs)
    except CommandArgumentError as exc:
        _log_usage(args.name, started, False)
        _fail(exc, 2)
    except BOUNDARY_ERRORS as exc:
        _log_usage(args.name, started, False)
        _fail(exc, 1)
    _log_usage(args.name, started, True)
    _emit_event(service, args.name, kwargs, result)
    print(json.dumps(result, default=str))
    return 0


# command name -> (handler, needs_odoo). ``needs_odoo`` commands exit 1 when the
# Odoo settings are unresolved. ``resync`` is local-first (its odoo puller has its
# own check) and ``cmd`` surfaces a missing configuration as the JSON envelope.
_COMMANDS: dict[str, tuple[Callable[[CommandService, Any], int], bool]] = {
    "normalize": (run_normalize, True),
    "get-employee-id": (run_get_employee_id, True),
    "upload": (run_upload, True),
    "resync": (run_resync, False),
    "log-event": (run_log_event, False),
    "cmd": (run_cmd, False),
}

# ``log-event`` is hook plumbing: logging it would double every hook firing.
# ``cmd`` is logged per registry command instead (#1204).
_USAGE_SKIPPED = {"log-event", "cmd"}


def _run_logged(
    handler: Callable[[CommandService, Any], int],
    service: CommandService,
    args: argparse.Namespace,
) -> int:
    """Run ``handler`` and write one ``cli_command`` usage line for it."""
    if args.command in _USAGE_SKIPPED:
        return handler(service, args)
    declare_usage(
        [
            {"kind": "cli_command", "name": n, "context": {"source": "odoo_client"}}
            for n in _COMMANDS
            if n not in _USAGE_SKIPPED
        ]
    )
    started, code = time.monotonic(), 1
    try:
        code = handler(service, args)
        return code
    finally:
        record_usage(
            "cli_command",
            args.command,
            source="odoo_client",
            ok=code == 0,
            ms=int((time.monotonic() - started) * 1000),
        )


def dispatch(args: argparse.Namespace) -> int:
    """Build the service, guard the Odoo configuration and run the handler."""
    handler, needs_odoo = _COMMANDS[args.command]
    service = bootstrap()
    if needs_odoo:
        try:
            assert_configured(service.services.config)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    try:
        return _run_logged(handler, service, args)
    except CliExit as stop:
        return stop.code
