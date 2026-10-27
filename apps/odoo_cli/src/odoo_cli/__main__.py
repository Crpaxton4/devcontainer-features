"""CLI companion for Odoo task time-tracking.

Usage:
    python -m odoo_cli [subcommand] [args]

Subcommands:
    normalize [--apply]     Detect and merge duplicate timesheet entries
    log-event               Record a Claude Code hook event into local state
    get-employee-id         Print the hr.employee id for the current user
    resync [--sources ...]  Reconcile local events against git/GitHub/Odoo chatter
    upload [--start ...]    Bill derived sessions to Odoo (headless TUI upload)
    cmd <name> [--args J]   Dispatch any registry command; stdout is one JSON doc
    cmd --list [--json]     List every registry command as {name, description}
"""

import argparse
import json
import sys
import time
from datetime import date, datetime
from typing import Any, Callable, NamedTuple, NoReturn, Optional

from odoo_client._utils import declare_usage, record_usage
from odoo_dev_core import CommandService
from odoo_dev_core.attribution import normalize_repo_label
from odoo_dev_core.registry import CommandArgumentError
from odoo_dev_core.sessionization import EventType
from odoo_dev_core.sessionization.persistence import (
    UnknownEventSourceError,
    source_to_event_type,
)
from odoo_dev_core.telemetry import (
    BOUNDARY_ERRORS,
    emit_tool_event,
    error_payload,
    is_dispatched_run,
)

from .bootstrap import OdooClient, assert_configured, bootstrap, build_client


def _assert_env() -> None:
    try:
        assert_configured()
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


class _LazyOdooClient:
    """Defer :class:`OdooClient` construction until an RPC member is first used.

    The :class:`~odoo_dev_core.registry.CommandService` the CLI builds requires an
    ``RpcClient``, but several subcommands resolve only local commands
    (a dry-run ``normalize``) that never
    reach Odoo. Wrapping the client behind first use lets the CLI build one
    service for every subcommand without eagerly constructing — and resolving
    connection settings for — a client the local-only paths would never touch.
    The instance is cached, so the first RPC call builds the real client and
    every later call reuses it. It structurally satisfies
    :class:`~odoo_dev_core.ports.RpcClient`.
    """

    def __init__(self) -> None:
        self._client: Optional[OdooClient] = None

    def _resolve(self) -> OdooClient:
        """Build the real client on first use and cache it thereafter."""
        if self._client is None:
            self._client = build_client()
        return self._client

    @property
    def uid(self) -> int:
        return self._resolve().uid

    def execute(self, model: str, method: str, *args, **kwargs):
        return self._resolve().execute(model, method, *args, **kwargs)

    def __getitem__(self, model_name: str):
        return self._resolve()[model_name]


def _build_service() -> CommandService:
    """Build the shared command service over the lazy Odoo client.

    The one wiring seam every subcommand routes through (and the tests patch).
    """
    return bootstrap(client=_LazyOdooClient())


def cmd_normalize(service: CommandService, args: argparse.Namespace) -> None:
    """Detect and optionally merge duplicate timesheet entries for same task+date."""
    report = service.execute("normalize_timesheets", apply=args.apply)
    groups = report["groups"]
    if not groups:
        print("No duplicate timesheet entries found.")
        return

    for group in groups:
        print(
            f"Task {group['task_id']!r} on {group['day']}: "
            f"{group['count']} entries totalling {group['total_hours']:.2f}h  "
            f"timesheet_ids={group['timesheet_ids']}"
        )
        if group["merged_into"] is not None:
            print(f"  -> Merged into timesheet {group['merged_into']}.")

    if not report["applied"]:
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


def cmd_log_event(service: CommandService, args: argparse.Namespace) -> None:
    """Record a single Claude Code hook event into the local ``events`` table.

    The event write is routed through the ``log_event`` command - the single
    owner of the ``events`` append (issue #407). Only the interface-specific
    resolution stays here: ``--source`` validation and ``--payload`` parsing.

    ``--task-id`` is handed over as the explicit hint and ``--attach-active-run``
    selects whether an unhinted event falls back to the stated branch.

    ``--branch`` and ``--repo`` are passed through when the hook shim states
    them: the shim reads both from the session's authoritative ``cwd``, which is
    *not* this process's cwd, so the command cannot re-derive them itself. The
    branch additionally recovers the task id from the ``<task-id>-<slug>``
    convention and stops hook events landing in triage (#574/#575). Omitting
    either leaves the command to resolve it from the cwd for the provenance
    column only (#509), with no attribution recovery.
    """
    event_type = _resolve_source(args.source)
    payload = _parse_payload(args.payload)
    service.execute(
        "log_event",
        source=args.source,
        subject=args.subject,
        payload=payload,
        task_ids=args.task_id,
        repo=normalize_repo_label(args.repo),
        branch=args.branch,
        timestamp=args.timestamp,
        attach_active_run=args.attach_active_run,
    )
    print(f"Logged event {args.source!r} ({event_type.name}).")


_RESYNC_SOURCES = ("git", "github", "odoo")


def _parse_resync_sources(raw: str) -> list[str]:
    """Return the requested resync pullers in stable order, ignoring unknowns."""
    requested = {token.strip() for token in raw.split(",") if token.strip()}
    return [source for source in _RESYNC_SOURCES if source in requested]


def _odoo_configured() -> bool:
    """Return whether the Odoo connection settings resolve.

    The git/github pullers never need Odoo, so a resync that also requests
    ``odoo`` where the SDK is unconfigured must degrade to a skip notice rather
    than aborting the whole command. The two failures
    :func:`~odoo_cli.bootstrap.assert_configured` raises are caught by name
    (#642).
    """
    try:
        assert_configured()
    except ValueError:
        return False
    return True


def _found_suffix(result: dict) -> str:
    """Render a success line's coverage parenthetical from the summary counters."""
    if "found" not in result:
        return ""
    suffix = f"found {result['found']}"
    if "repos" in result:
        suffix += f" in {result['repos']} repos"
    if result.get("failed_repos"):
        suffix += f"; {result['failed_repos']} failed"
    return suffix


def _format_resync_line(source: str, result: dict) -> str:
    """Format one per-source resync result line for the CLI.

    ``error`` (tooling entirely unusable, #652) and ``skipped`` (optional source
    absent) render distinctly; a success line carries the per-source coverage
    counters, flags newly stored reviews that resolved no task id (#653), any
    search-truncation warnings, and a range-ignored note.
    """
    if "error" in result:
        return f"{source}: error ({result['error']})"
    if "skipped" in result:
        return f"{source}: skipped ({result['skipped']})"
    line = f"{source}: inserted {result['inserted']}"
    suffix = _found_suffix(result)
    if suffix:
        line += f" ({suffix})"
    unattributed = result.get("unattributed_reviews") or []
    if unattributed:
        line += f"; WARNING: {len(unattributed)} review event(s) resolved no task id"
    for warning in result.get("warnings", []):
        line += f"; WARNING: {warning}"
    if result.get("note"):
        line += f"; note: {result['note']}"
    return line


def cmd_resync(service: CommandService, args: argparse.Namespace) -> None:
    """Reconcile local event state against git/GitHub/Odoo, directory-agnostic.

    Local-first: the git puller discovers every repo under the cwd, the github
    puller searches account-wide, and the odoo puller is guarded by a
    capability check. ``--start``/``--end`` bound the capture window. Each
    puller is idempotent and prints a per-source line; after ALL lines print,
    any ``error`` result exits nonzero so scripted callers see the failure.
    """
    selected = _parse_resync_sources(args.sources)
    if not selected:
        return
    runnable = [s for s in selected if s != "odoo" or _odoo_configured()]
    summary: dict[str, dict] = {}
    if runnable:
        summary = service.execute(
            "resync", sources=",".join(runnable), start=args.start, end=args.end
        )
    if "odoo" in selected and "odoo" not in runnable:
        summary["odoo"] = {"skipped": "odoo sdk not configured"}
    failed = False
    for source, result in summary.items():
        print(_format_resync_line(source, result))
        for label in result.get("unattributed_reviews", []):
            print(f"unattributed review: {label}", file=sys.stderr)
        failed = failed or "error" in result
    if failed:
        sys.exit(1)


def _print_upload_summary(result: dict, dry_run: bool) -> None:
    """Print the per-session upload rows and a trailing totals line.

    :param result: The ``upload_sessions`` summary dict.
    :param dry_run: Whether the run was a dry run (changes the verb printed).
    """
    verb = "would bill" if dry_run else "billed"
    for row in result["rows"]:
        target = "(dry run)" if dry_run else f"-> timesheet {row['timesheet_id']}"
        print(
            f"  task {row['task_id']}  {row['session_key']}  "
            f"{row['hours']:.2f}h  {target}"
        )
    print(
        f"{verb} {result['uploaded']} session(s); "
        f"skipped {result['skipped']} (no task id); "
        f"retired {result['retired']} orphaned upload(s)."
    )


def _parse_iso_date(value: str) -> str:
    """Validate an ISO ``YYYY-MM-DD`` argparse value, returning it unchanged.

    :raises argparse.ArgumentTypeError: When ``value`` is not a valid ISO date,
        so argparse reports the error cleanly and exits with status 2 (matching
        ``--timestamp``'s behavior) instead of a raw traceback downstream.
    """
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO date: {value!r}") from exc
    return value


def cmd_upload(service: CommandService, args: argparse.Namespace) -> None:
    """Bill derived sessions headlessly, sharing the TUI's upload path (#354).

    Runs the ``upload`` command - the same pipeline the TUI's ``u`` key does:
    ``query_sessions`` derives the sessions for the optional date range, then
    the shared upload loop reconciles each session's hours and sweeps the
    window's stale upload mappings. ``--dry-run`` previews the billable set
    without writing to Odoo.
    """
    result = service.execute(
        "upload", start_date=args.start, end_date=args.end, dry_run=args.dry_run
    )
    _print_upload_summary(result, args.dry_run)


def cmd_get_employee_id(service: CommandService, _args: argparse.Namespace) -> None:
    """Print the ``hr.employee`` id for the authenticated Odoo user (#499).

    Dispatches the ``get_employee_id`` command - the same resolver the
    unattended export path uses - so the CLI and that path agree on "who is
    this?". Needs an Odoo connection to resolve the uid on a cold cache.
    """
    employee_id = service.execute("get_employee_id")
    print(f"employee_id={employee_id}")


def _cmd_fail(exc: BaseException, code: int) -> NoReturn:
    """Print the shared dispatch error envelope as stdout's one JSON doc; exit.

    The envelope is built by the same :func:`~odoo_dev_core.telemetry.
    error_payload` the MCP error boundary uses, so a script
    driving ``odoo-dev cmd`` sees exactly the ``{"error": {"type", "message"}}``
    shape an MCP caller would. Exit ``1`` marks a boundary (caller-actionable)
    failure, ``2`` a usage error — unknown name, malformed ``--args``, bad
    kwargs.
    """
    print(json.dumps(error_payload(exc), default=str))
    sys.exit(code)


def _cmd_list_entries(service: CommandService) -> list[dict[str, str]]:
    """Return every CLI command as a ``{name, description}`` row.

    Sorted by name so the output is stable - this listing is the contract-gate
    ground truth for the CI gate that asserts the dispatchable surface (#713).
    """
    return [
        {"name": spec.id, "description": spec.description}
        for spec in service.registry.specs("cli")
    ]


def _print_cmd_list(entries: list[dict[str, str]], as_json: bool) -> None:
    """Print the command listing: a JSON array, or a human-readable table."""
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


def _parse_cmd_kwargs(raw: Optional[str]) -> dict[str, Any]:
    """Parse ``--args`` into the command's kwargs; usage-fail (exit 2) if bad."""
    if raw is None:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        _cmd_fail(ValueError(f"malformed --args JSON: {exc}"), 2)
    if not isinstance(parsed, dict):
        _cmd_fail(ValueError("--args must be a JSON object"), 2)
    return parsed


def _check_cmd(service: CommandService, name: str) -> None:
    """Usage-fail (exit 2) when ``name`` is not a registered command."""
    try:
        service.registry.get(name)
    except KeyError:
        _cmd_fail(ValueError(f"unknown command: {name!r} (see 'cmd --list')"), 2)


def _emit_cmd_event(
    service: CommandService, name: str, kwargs: dict[str, Any], result: Any
) -> None:
    """Emit the one ``source="agent"`` event a successful ``cmd`` dispatch owns.

    Routed through the same :func:`~odoo_dev_core.telemetry.emit_tool_event`
    pipeline the MCP server uses, with ``via="cli"`` marking the dispatch
    surface in the payload. Best-effort like the MCP wrapper: a failing state
    store must never turn a successful dispatch into an error. A dispatched run
    (``ODOO_UNATTENDED``) emits nothing: the run record measures it (#1065).
    """
    if is_dispatched_run(service.services.env):
        return
    try:
        emit_tool_event(service, name, kwargs, result, via="cli")
    except Exception:
        pass


def _log_cmd_usage(name: str, started: float, ok: bool) -> None:
    """Write one ``sdk_command`` usage line (#1204): names and outcome only."""
    record_usage(
        "sdk_command",
        name,
        source="odoo_client",
        via="cli",
        ok=ok,
        ms=int((time.monotonic() - started) * 1000),
    )


def cmd_cmd(args: argparse.Namespace) -> None:
    """Generic dispatcher: run any registered command, stdout one JSON document.

    ``cmd --list`` enumerates the full registered CLI surface - gated commands
    included (process trust: a shell that can run this CLI already owns the
    process) - and ``cmd <name> --args '<json>'`` dispatches one command with
    the JSON object as kwargs. Success prints the raw command result
    (``json.dumps(..., default=str)``) and exits 0; a boundary error prints the
    shared MCP error envelope and exits 1; a usage error (unknown name,
    malformed ``--args``, kwargs that do not fit the command) prints the same
    envelope shape and exits 2.

    The service is built over :class:`_LazyOdooClient`, so a command that never
    touches Odoo dispatches with no Odoo configuration present; a command that
    does need Odoo surfaces its config/connection failure through the boundary
    as the JSON envelope rather than the ``_assert_env`` plaintext.
    """
    service = _build_service()
    if args.list_commands:
        _print_cmd_list(_cmd_list_entries(service), args.as_json)
        return
    if args.name is None:
        _cmd_fail(ValueError("a command name (or --list) is required"), 2)
    kwargs = _parse_cmd_kwargs(args.cmd_args)
    _check_cmd(service, args.name)
    started = time.monotonic()
    try:
        result = service.execute(args.name, **kwargs)
    except CommandArgumentError as exc:
        _log_cmd_usage(args.name, started, False)
        _cmd_fail(exc, 2)
    except BOUNDARY_ERRORS as exc:
        _log_cmd_usage(args.name, started, False)
        _cmd_fail(exc, 1)
    _log_cmd_usage(args.name, started, True)
    _emit_cmd_event(service, args.name, kwargs, result)
    print(json.dumps(result, default=str))


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all subcommands declared.

    :returns: Configured parser for the time-tracking companion CLI.
    :rtype: argparse.ArgumentParser
    """
    parser = argparse.ArgumentParser(
        prog="odoo-dev",
        description="Odoo task time-tracking companion CLI",
    )
    subparsers = parser.add_subparsers(dest="command")

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

    subparsers.add_parser(
        "get-employee-id", help="Print the hr.employee id for the current user"
    )

    resync_p = subparsers.add_parser(
        "resync", help="Reconcile local events against git/GitHub/Odoo chatter"
    )
    resync_p.add_argument(
        "--sources",
        default="git,github,odoo",
        help=("Comma-separated subset of git,github,odoo (default: git,github,odoo)"),
    )
    resync_p.add_argument(
        "--start",
        type=_parse_iso_date,
        default=None,
        help="Inclusive ISO start date (YYYY-MM-DD) for the capture window",
    )
    resync_p.add_argument(
        "--end",
        type=_parse_iso_date,
        default=None,
        help="Inclusive ISO end date (YYYY-MM-DD) for the capture window",
    )

    upload_p = subparsers.add_parser(
        "upload", help="Bill derived sessions to Odoo (headless TUI upload path)"
    )
    upload_p.add_argument(
        "--start",
        type=_parse_iso_date,
        default=None,
        help="Inclusive ISO start date (YYYY-MM-DD)",
    )
    upload_p.add_argument(
        "--end",
        type=_parse_iso_date,
        default=None,
        help="Inclusive ISO end date (YYYY-MM-DD)",
    )
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
    cmd_p.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="With --list, print the listing as a JSON array",
    )
    return parser


class _Ctx(NamedTuple):
    """The shared peers a subcommand handler may draw on.

    ``service`` is built for every command except ``cmd``, which builds its own
    so it can surface a configuration failure as the JSON envelope.
    """

    args: argparse.Namespace
    service: Optional[CommandService]


# The single routing table: command name -> (handler, needs_odoo). A
# ``needs_odoo=False`` command skips the capability assert because it touches
# only local tracker state - ``log-event`` writes it, and ``resync`` is
# local-first (its git/github pullers never touch Odoo, and the odoo puller sits
# behind its own capability check). Adding a subcommand means one entry here
# (plus its parser).
_COMMANDS: dict[str, tuple[Callable[[_Ctx], None], bool]] = {
    "normalize": (lambda c: cmd_normalize(c.service, c.args), True),
    "get-employee-id": (lambda c: cmd_get_employee_id(c.service, c.args), True),
    "upload": (lambda c: cmd_upload(c.service, c.args), True),
    "resync": (lambda c: cmd_resync(c.service, c.args), False),
    "log-event": (lambda c: cmd_log_event(c.service, c.args), False),
    # ``cmd`` is needs_odoo=False on purpose: it builds its own service so a
    # local-only command never touches env config, and an Odoo-needing command
    # surfaces its config error through the boundary as the JSON envelope
    # (never _assert_env plaintext).
    "cmd": (lambda c: cmd_cmd(c.args), False),
}

# The local-only command names, derived from the table so routing stays a single
# source of truth.
_LOCAL_ONLY = {name for name, (_, needs_odoo) in _COMMANDS.items() if not needs_odoo}


# Subcommands the usage log skips (#1204). ``log-event`` is plumbing: every hook
# shim shells out to it, so recording it would log each hook firing a second time
# and spend an interpreter start per tool call for a command no agent chose. The
# hook scripts record their own run instead. ``cmd`` is logged per registry
# command (kind ``sdk_command``), which is the name a reader can match.
_USAGE_SKIPPED = {"log-event", "cmd"}


def _log_cli_usage(command: str, started: float, ok: bool) -> None:
    """Write one ``cli_command`` usage line for a finished subcommand."""
    if command in _USAGE_SKIPPED:
        return
    record_usage(
        "cli_command",
        command,
        source="odoo_client",
        ok=ok,
        ms=int((time.monotonic() - started) * 1000),
    )


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    entry = _COMMANDS.get(args.command)
    if entry is None:
        parser.print_help()
        sys.exit(1)
    started = time.monotonic()
    ok = False
    if args.command not in _USAGE_SKIPPED:
        # Keeps the declaration of every subcommand current without a producer
        # of its own; skipped for the hook-hot ``log-event`` path.
        declare_usage(
            [
                {"kind": "cli_command", "name": n, "context": {"source": "odoo_client"}}
                for n in _COMMANDS
                if n not in _USAGE_SKIPPED
            ]
        )
    try:
        _run_command(entry, args)
        ok = True
    finally:
        _log_cli_usage(args.command, started, ok)


def _run_command(
    entry: tuple[Callable[..., Any], bool], args: argparse.Namespace
) -> None:
    handler, needs_odoo = entry
    if args.command == "cmd":
        handler(_Ctx(args, None))
        return
    if needs_odoo:
        _assert_env()
    handler(_Ctx(args, _build_service()))


if __name__ == "__main__":
    main()
