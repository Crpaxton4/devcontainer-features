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

# The CLI no longer imports the data-layer adapters directly (#717): the
# pullers come through the core resync command module — the single writer of the resync workflow and the sanctioned
# importer of the external-sync adapters — and the event-source vocabulary
# through the core log-event command module. The puller names stay bound on
# THIS module because the pinned CLI tests patch them here (the
# delegating-wrapper contract): every resync puller below resolves through
# these module globals at call time.
from odoo_dev_core.commands.builtin.resync import (
    ResyncCommand,
    sync_git_log,
    sync_github,
    sync_odoo_chatter,
)
from odoo_dev_core.commands.log_event import (
    UnknownEventSourceError,
    normalize_repo_label,
    source_to_event_type,
)

# The composition root is the CLI's only below-core dependency (#716): the
# concrete OdooClient / LocalConfig / EventStore constructors are
# obtained from it rather than from the data layer, and every registry is
# wired through ``bootstrap`` (via ``_build_registry`` below).
from odoo_client._utils import declare_usage, record_usage
from .bootstrap import (
    LocalConfig,
    OdooClient,
    assert_configured,
    bootstrap,
    build_client,
)
from .bootstrap import EventStore as TaskStateDB
from odoo_dev_core.commands import Command, LogEventCommand, Registry
from odoo_dev_core.commands.dispatch_telemetry import (
    _BOUNDARY_ERRORS,
    _emit_tool_event,
    _error_payload,
    _is_dispatched_run,
)
from odoo_dev_core.sessionization import EventType
from odoo_dev_core.billing.upload import upload_sessions


def _assert_env() -> None:
    try:
        assert_configured()
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


class _LazyOdooClient:
    """Defer :class:`OdooClient` construction until an RPC member is first used.

    The shared :class:`~odoo_dev_core.commands.Registry` the CLI builds requires an
    ``RpcClient``, but several subcommands resolve only local commands
    (a dry-run ``normalize``) that never
    reach Odoo. Wrapping the client behind first use lets the CLI build one
    registry for every subcommand without eagerly constructing — and resolving
    connection settings for — a client the local-only paths would never touch.
    The instance is cached, so the first RPC call builds the real client and
    every later call reuses it. It structurally satisfies
    :class:`~odoo_dev_core.commands.protocols.RpcClient`.
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


def _build_registry(
    client, state: Optional[TaskStateDB] = None, config: Optional[LocalConfig] = None
) -> Registry:
    """Build the shared built-in command registry, exactly like MCP/TUI.

    A thin delegate to the single composition root
    (:func:`odoo_cli.bootstrap.bootstrap`, #716), kept as the CLI's one
    registry-factory seam so every subcommand — and the tests that patch it —
    routes through the same wiring. The CLI dispatches every command through
    this registry instead of constructing command classes by hand, so the
    command layer is the single integration point and no subcommand reaches
    into ``state`` internals. A ``None`` ``state`` lets each command resolve
    its own; a ``None`` ``config`` lets the
    composition root load and inject the resolved settings once.
    """
    return bootstrap(client=client, state=state, config=config)


def cmd_normalize(registry: Registry, args: argparse.Namespace) -> None:
    """Detect and optionally merge duplicate timesheet entries for same task+date."""
    report = registry["normalize_timesheets"].execute(apply=args.apply)
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


def cmd_log_event(args: argparse.Namespace) -> None:
    """Record a single Claude Code hook event into the local ``events`` table.

    The event write is routed through :class:`~odoo_dev_core.commands.log_event.
    LogEventCommand` — the single command-layer owner of the ``events``
    append (issue #407) — so this subcommand no longer constructs an
    ``EventRecord`` and calls ``add_event`` inline. Only the interface-specific
    resolution stays here: ``--source`` validation and ``--payload`` parsing.

    The task-scope policy that used to live in this module was folded into the
    command (#507) so the CLI and the MCP dispatch wrapper attribute events by
    one rule instead of two: ``--task-id`` is handed over as the explicit hint
    and ``--attach-active-run`` selects whether an unhinted event falls back to
    the stated branch, which keeps this subcommand's documented flag semantics —
    and the hook shim's contract — unchanged.

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
    LogEventCommand(state=TaskStateDB()).execute(
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


def _resync_odoo(
    db: TaskStateDB, start: Optional[date] = None, end: Optional[date] = None
) -> dict:
    """Run the Odoo chatter puller, or skip cleanly when the capability check fails.

    The git/github pullers never need Odoo, so a resync that also requests
    ``odoo`` where the SDK is unconfigured must degrade to a skip notice rather
    than aborting the whole command. The :class:`OdooClient` is built only after
    the assert passes, honoring the lazy-client contract.

    The two failures :func:`~odoo_cli.bootstrap.assert_configured` raises
    are caught by name (#642).
    """
    try:
        assert_configured()
    except ValueError:
        return {"skipped": "odoo sdk not configured"}
    config = LocalConfig.load()
    return sync_odoo_chatter(build_client(config), db, config, start=start, end=end)


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


def _cli_resync_pullers() -> dict[str, Callable[..., dict]]:
    """Build the CLI's puller table for the shared :class:`ResyncCommand` (#717).

    The *workflow* — source selection order, range parsing, the per-source
    summary shape — has one writer, :meth:`ResyncCommand.execute`; only the
    per-source semantics differ here, preserved exactly as the pinned CLI
    tests require:

    * git/github stay local-only (NO Odoo client, so task-id validation is
      skipped), unlike the registry default which passes the injected client;
    * odoo stays behind the lazy capability guard (:func:`_resync_odoo`);

    Every lambda resolves the puller names through this module's globals at
    call time, so patching ``cli.__main__.sync_git_log`` (etc.) still
    intercepts the call — the delegating-wrapper contract.
    """
    return {
        "git": lambda cmd, start, end: sync_git_log(
            cmd.state, cmd.config, start=start, end=end
        ),
        "github": lambda cmd, start, end: sync_github(
            cmd.state, cmd.config, start=start, end=end
        ),
        "odoo": lambda cmd, start, end: _resync_odoo(cmd.state, start=start, end=end),
    }


def cmd_resync(args: argparse.Namespace) -> None:
    """Reconcile local event state against git/GitHub/Odoo, directory-agnostic.

    Local-first: the git puller discovers every repo under the cwd, the github
    puller searches account-wide, and the odoo puller is guarded and constructed
    lazily. ``--start``/``--end`` bound the capture window. Each puller is
    idempotent and prints a per-source line; after ALL lines print, any
    ``error`` result exits nonzero so scripted callers see the failure.

    Since #717 the orchestration is no longer duplicated inline: the CLI
    dispatches the registry's :class:`ResyncCommand` (the one writer of the
    workflow), injecting its local-first puller table
    (:func:`_cli_resync_pullers`) so flag semantics and output stay exactly
    as before.
    """
    selected = _parse_resync_sources(args.sources)
    if not selected:
        return
    command = ResyncCommand(
        _LazyOdooClient(),
        state=TaskStateDB(),
        config=LocalConfig.load(),
        pullers=_cli_resync_pullers(),
    )
    summary = command.execute(
        sources=",".join(selected), start=args.start, end=args.end
    )
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


def cmd_upload(
    args: argparse.Namespace,
    registry: Registry,
    client,
    db: TaskStateDB,
    config: LocalConfig,
) -> None:
    """Bill derived sessions headlessly, sharing the TUI's upload path (#354).

    Runs the same pipeline the TUI's ``u`` key does — the shared registry's
    ``query_sessions`` command derives the sessions for the optional date range,
    then the shared :func:`~odoo_dev_core.billing.upload.upload_sessions` loop
    reconciles each session's hours and sweeps the window's stale upload mappings
    — so a non-interactive ``odoo-sdk upload`` bills exactly the rows the TUI
    would. ``--dry-run`` previews the billable set without writing to Odoo.
    """
    sessions = registry["query_sessions"].execute(
        start_date=args.start, end_date=args.end, include_events=False
    )
    result = upload_sessions(
        client,
        db,
        sessions,
        start_date=args.start,
        end_date=args.end,
        dry_run=args.dry_run,
        config=config,
    )
    _print_upload_summary(result, args.dry_run)


def cmd_get_employee_id(registry: Registry, _args: argparse.Namespace) -> None:
    """Print the ``hr.employee`` id for the authenticated Odoo user (#499).

    Dispatches through the shared registry's ``get_employee_id`` command — the
    same resolver the unattended export path uses — so the CLI and that path agree
    on "who is this?". Needs an Odoo connection to resolve the uid on a cold cache.
    """
    employee_id = registry["get_employee_id"].execute()
    print(f"employee_id={employee_id}")


def _cmd_fail(exc: BaseException, code: int) -> NoReturn:
    """Print the shared dispatch error envelope as stdout's one JSON doc; exit.

    The envelope is built by the same :func:`~odoo_dev_core.commands.
    dispatch_telemetry._error_payload` the MCP error boundary uses, so a script
    driving ``odoo-sdk cmd`` sees exactly the ``{"error": {"type", "message"}}``
    shape an MCP caller would. Exit ``1`` marks a boundary (caller-actionable)
    failure, ``2`` a usage error — unknown name, malformed ``--args``, bad
    kwargs.
    """
    print(json.dumps(_error_payload(exc), default=str))
    sys.exit(code)


def _cmd_list_entries(registry: Registry) -> list[dict[str, str]]:
    """Return every registry command as a ``{name, description}`` row.

    Sorted by name so the output is stable — this listing is the contract-gate
    ground truth for the CI gate that asserts the dispatchable surface (#713).
    """
    return [
        {"name": name, "description": command.description}
        for name, command in sorted(registry.items(), key=lambda item: item[0])
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


def _resolve_cmd(registry: Registry, name: str) -> Command:
    """Resolve ``name`` on the registry; usage-fail (exit 2) when unknown."""
    try:
        return registry[name]
    except KeyError:
        _cmd_fail(ValueError(f"unknown command: {name!r} (see 'cmd --list')"), 2)


def _emit_cmd_event(
    registry: Registry, name: str, kwargs: dict[str, Any], result: Any
) -> None:
    """Emit the one ``source="agent"`` event a successful ``cmd`` dispatch owns.

    Routed through the same :func:`~odoo_dev_core.commands.dispatch_telemetry.
    _emit_tool_event` pipeline the MCP server uses, with ``via="cli"`` marking
    the dispatch surface in the payload. Best-effort like the MCP wrapper: a
    failing state store must never turn a successful dispatch into an error.
    A dispatched run (``ODOO_UNATTENDED``) emits nothing: the run record
    measures it (#1065).
    """
    if _is_dispatched_run():
        return
    try:
        _emit_tool_event(registry.state_client, name, kwargs, result, via="cli")
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
    """Generic dispatcher: run any registry command, stdout one JSON document.

    ``cmd --list`` enumerates the full registered surface — gated MCP tools
    included (process trust: a shell that can run this CLI already owns the
    process) — and ``cmd <name> --args '<json>'`` dispatches one command with
    the JSON object as kwargs. Success prints the raw command result
    (``json.dumps(..., default=str)``) and exits 0; a boundary error prints the
    shared MCP error envelope and exits 1; a usage error (unknown name,
    malformed ``--args``, a ``TypeError`` from bad kwargs) prints the same
    envelope shape and exits 2.

    The registry is built over :class:`_LazyOdooClient` with no eager state or
    config, so a command that never touches Odoo dispatches with no Odoo
    configuration present; a command that does need Odoo surfaces its
    config/connection failure through the boundary as the JSON envelope rather
    than the ``_assert_env`` plaintext.
    """
    registry = _build_registry(_LazyOdooClient())
    if args.list_commands:
        _print_cmd_list(_cmd_list_entries(registry), args.as_json)
        return
    if args.name is None:
        _cmd_fail(ValueError("a command name (or --list) is required"), 2)
    kwargs = _parse_cmd_kwargs(args.cmd_args)
    command = _resolve_cmd(registry, args.name)
    started = time.monotonic()
    try:
        result = command.execute(**kwargs)
    except TypeError as exc:
        _log_cmd_usage(args.name, started, False)
        _cmd_fail(exc, 2)
    except _BOUNDARY_ERRORS as exc:
        _log_cmd_usage(args.name, started, False)
        _cmd_fail(exc, 1)
    _log_cmd_usage(args.name, started, True)
    _emit_cmd_event(registry, args.name, kwargs, result)
    print(json.dumps(result, default=str))


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all subcommands declared.

    :returns: Configured parser for the time-tracking companion CLI.
    :rtype: argparse.ArgumentParser
    """
    parser = argparse.ArgumentParser(
        prog="python -m odoo_cli",
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

    ``registry`` / ``client`` / ``db`` / ``config`` are built only for the
    Odoo-backed commands; local-only handlers receive ``None`` for them and use
    just ``args`` (they construct their own local ``TaskStateDB``).
    """

    args: argparse.Namespace
    registry: Optional[Registry]
    client: object
    db: Optional[TaskStateDB]
    config: Optional[LocalConfig]


# The single routing table: command name -> (handler, needs_odoo). A
# ``needs_odoo=False`` command skips the capability assert and builds no
# OdooClient because it touches only local tracker state — ``log-event`` writes
# it, and ``resync`` is local-first (its
# git/github pullers never touch Odoo and it constructs an OdooClient lazily,
# behind its own capability assert, only when ``odoo`` is requested). Adding a
# subcommand means one entry here (plus its parser).
_COMMANDS: dict[str, tuple[Callable[[_Ctx], None], bool]] = {
    "normalize": (lambda c: cmd_normalize(c.registry, c.args), True),
    "get-employee-id": (lambda c: cmd_get_employee_id(c.registry, c.args), True),
    "upload": (
        lambda c: cmd_upload(c.args, c.registry, c.client, c.db, c.config),
        True,
    ),
    "resync": (lambda c: cmd_resync(c.args), False),
    "log-event": (lambda c: cmd_log_event(c.args), False),
    # ``cmd`` is needs_odoo=False on purpose: it builds its own registry over a
    # _LazyOdooClient so a local-only command never constructs an OdooClient or
    # touches env config, and an Odoo-needing command surfaces its config error
    # through the boundary as the JSON envelope (never _assert_env plaintext).
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
    if not needs_odoo:
        handler(_Ctx(args, None, None, None, None))
        return
    _assert_env()
    client = _LazyOdooClient()
    db = TaskStateDB()
    config = LocalConfig.load()
    registry = _build_registry(client, state=db, config=config)
    handler(_Ctx(args, registry, client, db, config))


if __name__ == "__main__":
    main()
