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
    discover                List active runs in the central tracker DB
    abort <run_id>          Abort a stale run in the central tracker DB
    close <task_id>         Close a task's run into the terminal CLOSED state
    get-employee-id         Print the hr.employee id for the current user
    reap [--older-than 12h] Bulk-abort every stale run in the central tracker DB
    resync [--sources ...]  Reconcile local events against git/GitHub/Odoo chatter
    upload [--start ...]    Bill derived sessions to Odoo (headless TUI upload)
    prune [--older-than N]  Delete aged hook events past a retention horizon
"""

import argparse
import json
import math
import sys
from datetime import date, datetime
from typing import Callable, NamedTuple, Optional

from odoo_sdk.adapters import (
    GoogleAPIError,
    GoogleAuthError,
    UnknownEventSourceError,
    source_to_event_type,
    sync_git_log,
    sync_github,
    sync_gmail,
    sync_google_calendar,
    sync_odoo_chatter,
)
from odoo_sdk.client import OdooClient
from odoo_sdk.commands import LogEventCommand, Registry
from odoo_sdk.commands.builtin import register_builtins
from odoo_sdk.sessionization import EventType
from odoo_sdk.state import LocalConfig, TrackerStateMissingError
from odoo_sdk.state import LocalStateClient as TaskStateDB
from odoo_sdk.utilities.env import (
    OdooDevcontainerRequiredError,
    assert_odoo_devcontainer,
)
from odoo_sdk.prune import execute_prune, plan_prune, resolve_horizon
from odoo_sdk.reap import (
    DEFAULT_REAP_THRESHOLD_HOURS,
    reap_run,
    stale_active_runs,
    threshold_from_hours,
)
from odoo_sdk.billing.upload import upload_sessions

def _assert_env() -> None:
    try:
        assert_odoo_devcontainer()
    except OdooDevcontainerRequiredError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


class _LazyOdooClient:
    """Defer :class:`OdooClient` construction until an RPC member is first used.

    The shared :class:`~odoo_sdk.commands.Registry` the CLI builds requires an
    ``RpcClient``, but several subcommands resolve only local commands
    (``list``, ``report``, ``discover``, a dry-run ``normalize``) that never
    reach Odoo. Wrapping the client behind first use lets the CLI build one
    registry for every subcommand without eagerly constructing — and resolving
    connection settings for — a client the local-only paths would never touch.
    The instance is cached, so the first RPC call builds the real client and
    every later call reuses it. It structurally satisfies
    :class:`~odoo_sdk.commands.protocols.RpcClient`.
    """

    def __init__(self) -> None:
        self._client: Optional[OdooClient] = None

    def _resolve(self) -> OdooClient:
        """Build the real client on first use and cache it thereafter."""
        if self._client is None:
            self._client = OdooClient()
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

    The CLI dispatches every command through this registry instead of
    constructing command classes by hand, so the command layer is the single
    integration point and no subcommand reaches into ``state`` internals. A
    ``None`` ``state`` lets each command resolve its own (the local-only
    ``discover`` path).
    """
    return register_builtins(Registry(client, state_client=state, config=config))


def _fmt_row(row: dict, *, include_state: bool = True) -> str:
    parts = [
        f"[{row['id']}]",
        f"{row['task_name'][:40]:<40}",
        f"{row['project_name'][:25]:<25}",
    ]
    if include_state:
        parts.append(f"{row['state']:<18}")
    parts.append(row["elapsed"])
    return "  ".join(parts)


def _print_runs(runs: list[dict]) -> None:
    """Print the shared run table: header, rule, and one ``_fmt_row`` per run."""
    header = f"{'[ID]':<5}  {'Task':<40}  {'Project':<25}  {'State':<18}  Elapsed"
    print(header)
    print("-" * len(header))
    for row in runs:
        print(_fmt_row(row))


def cmd_list(registry: Registry, _args: argparse.Namespace) -> None:
    runs = registry["list_runs"].execute()
    if not runs:
        print("No active runs.")
        return
    _print_runs(runs)


def cmd_stop(registry: Registry, args: argparse.Namespace) -> None:
    """Force-stop one run through the shared :class:`StopRunCommand`.

    Routing through the command keeps the "upload owns hours" invariant: stopping
    a run only transitions it to STOPPED and records local session data — it never
    writes ``account.analytic.line`` ``unit_amount`` hours. The elapsed wall-clock
    is billed later by the upload/reconcile path (minimum floor + step rounding),
    so the same run bills identically whether stopped here or via the MCP tool.
    """
    result = registry["stop_run"].execute(args.run_id)
    if not result["found"]:
        print(f"Error: no run with id {args.run_id}.", file=sys.stderr)
        sys.exit(1)
    if result["already_stopped"]:
        print(f"Run {args.run_id} is already stopped.")
        return
    print(
        f"Stopped run {args.run_id}: {result['task_name']!r}  "
        f"elapsed={result['elapsed']}"
    )


def cmd_stop_all(registry: Registry, _args: argparse.Namespace) -> None:
    """Force-stop every active run through the shared :class:`StopAllRunsCommand`.

    As with :func:`cmd_stop`, each stop only transitions the run to STOPPED and
    never writes timesheet hours; the upload path owns all ``unit_amount`` writes.
    """
    stopped = registry["stop_all"].execute()
    if not stopped:
        print("Nothing to stop.")
        return
    for row in stopped:
        print(
            f"Stopped run {row['id']}: {row['task_name']!r}  "
            f"elapsed={row['elapsed']}"
        )


def cmd_report(registry: Registry, args: argparse.Namespace) -> None:
    runs = registry["report_runs"].execute(include_stopped=args.all)
    if not runs:
        print("No runs found.")
        return
    _print_runs(runs)


def cmd_normalize(registry: Registry, args: argparse.Namespace) -> None:
    """Detect and optionally merge duplicate timesheet entries for same task+date."""
    report = registry["normalize_timesheets"].execute(apply=args.apply)
    groups = report["groups"]
    if not groups:
        print("No duplicate timesheet entries found.")
        return

    for group in groups:
        print(
            f"Task {group['task_id']!r} ({group['task_name']}) on {group['day']}: "
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

    The event write is routed through :class:`~odoo_sdk.commands.log_event.
    LogEventCommand` — the single command-layer owner of the ``events``
    append (issue #407) — so this subcommand no longer constructs an
    ``EventRecord`` and calls ``add_event`` inline. Only the interface-specific
    resolution stays here: ``--source`` validation and ``--payload`` parsing.

    The task-scope policy that used to live in this module was folded into the
    command (#507) so the CLI and the MCP dispatch wrapper attribute events by
    one rule instead of two: ``--task-id`` is handed over as the explicit hint
    and ``--attach-active-run`` selects whether an unhinted event falls back to
    the active runs, which keeps this subcommand's documented flag semantics —
    and the hook shim's contract — unchanged. The repo label is no
    longer resolved here either; the command resolves it from the working tree
    (#509).

    ``--branch`` is passed through when the hook shim states it: the shim reads it
    from the session's authoritative cwd so the command can recover the task id
    from the ``<task-id>#<slug>`` convention and stop hook events landing in
    triage (#574/#575). Omitting it leaves the command to resolve the branch from
    the cwd for the provenance column only, with no attribution recovery.
    """
    event_type = _resolve_source(args.source)
    payload = _parse_payload(args.payload)
    LogEventCommand(state=TaskStateDB()).execute(
        source=args.source,
        subject=args.subject,
        payload=payload,
        task_ids=args.task_id,
        branch=args.branch,
        timestamp=args.timestamp,
        attach_active_run=args.attach_active_run,
    )
    print(f"Logged event {args.source!r} ({event_type.name}).")


def _discover_run_line(run: dict) -> str:
    """Format one active run row for the ``discover`` table."""
    flag = "STALE" if run["stale"] else ""
    return "  ".join(
        [
            f"[{run['run_id']}]",
            f"{run['task_name'][:30]:<30}",
            f"{run['project_name'][:25]:<25}",
            f"{run['state']:<18}",
            f"{run['started_at'][:19]:<19}",
            f"{flag:<5}",
        ]
    )


def cmd_discover(args: argparse.Namespace) -> None:
    """List every active run in the central tracker DB.

    Local-only: dispatches through the shared registry's ``discover_runs``
    command (a read-only central-DB query that needs no Odoo connection), so the
    CLI reaches this via the command layer rather than ``state.discovery``.
    """
    registry = _build_registry(_LazyOdooClient())
    runs = registry["discover_runs"].execute(stale_after_hours=args.stale_after_hours)
    if not runs:
        print("No active runs.")
        return
    header = (
        f"{'[ID]':<5}  {'Task':<30}  {'Project':<25}  {'State':<18}  "
        f"{'Started':<19}  Flag"
    )
    print(header)
    print("-" * len(header))
    for run in runs:
        print(_discover_run_line(run))


# git/github/odoo are the default pullers; gcal/gmail are opt-in Google sources
# (issue #370) that require host-provisioned credentials, so they are only run
# when named explicitly and never reached by the default source string.
_RESYNC_SOURCES = ("git", "github", "odoo", "gcal", "gmail")


def _parse_resync_sources(raw: str) -> list[str]:
    """Return the requested resync pullers in stable order, ignoring unknowns."""
    requested = {token.strip() for token in raw.split(",") if token.strip()}
    return [source for source in _RESYNC_SOURCES if source in requested]


def _resync_google(puller: Callable[..., dict], db: TaskStateDB) -> dict:
    """Run one Google puller, surfacing an unusable-credential error as a skip.

    The puller itself raises a single actionable :class:`GoogleAuthError` (or a
    ``ValueError`` for a rejected tick interval) rather than silently ingesting
    nothing; a transient REST failure surfaces as :class:`GoogleAPIError`. The
    CLI prints any of these as this source's skip reason so one misconfigured or
    momentarily-unreachable Google source never aborts the whole resync.
    """
    try:
        return puller(db, LocalConfig.load())
    except (GoogleAuthError, GoogleAPIError, ValueError) as exc:
        return {"skipped": str(exc)}


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
    return sync_odoo_chatter(OdooClient(), db, LocalConfig.load())


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
    db = TaskStateDB()
    config = LocalConfig.load()
    runners = {
        # git/github stay local-only in the CLI (no Odoo client, so task-id
        # validation is skipped); config supplies the resync window/authors.
        "git": lambda db: sync_git_log(db, config),
        "github": lambda db: sync_github(db, config),
        "odoo": _resync_odoo,
        "gcal": lambda db: _resync_google(sync_google_calendar, db),
        "gmail": lambda db: _resync_google(sync_gmail, db),
    }
    for source in sources:
        result = runners[source](db)
        print(_format_resync_line(source, result))


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
        f"excluded {result['excluded']} (aborted runs); "
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
    then the shared :func:`~odoo_sdk.billing.upload.upload_sessions` loop
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


def _positive_int(value: str) -> int:
    """Parse a strictly-positive ``--older-than`` day count (argparse type).

    A retention horizon must be at least one day: ``0`` (and anything negative)
    would set the cutoff to now or the future and prune every closed session, so
    it is rejected as a usage error (exit 2) rather than silently flushing the DB.
    This keeps the explicit flag consistent with ``prune_horizon_days``, where
    ``0`` means "auto-prune off".

    :raises argparse.ArgumentTypeError: When ``value`` is not a positive integer.
    """
    try:
        days = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid int value: {value!r}") from exc
    if days < 1:
        raise argparse.ArgumentTypeError(
            f"--older-than must be a positive number of days, got {days}"
        )
    return days


def _resolve_prune_days(
    args: argparse.Namespace, config: LocalConfig
) -> Optional[int]:
    """Return the horizon in days to prune to, or None when auto-prune is off.

    An explicit ``--older-than`` always wins; otherwise the configured
    ``prune_horizon_days`` (file ``[behavior]`` or ``ODOO_PRUNE_HORIZON_DAYS``)
    is honored, and an unset/zero default means "never auto-prune".
    """
    if args.older_than is not None:
        return args.older_than
    return resolve_horizon(config)


def cmd_prune(args: argparse.Namespace) -> None:
    """Delete aged hook events past a retention horizon, guarding uploads (#363).

    Local-only: plans the prune (see :func:`~odoo_sdk.prune.plan_prune`)
    so that no un-uploaded session's events and no still-tracked session's
    minimum-id key can ever be disturbed, then either previews (``--dry-run``) or
    executes the deletion and retires the ledger mappings of the fully-uploaded,
    fully-aged sessions it removed. With no ``--older-than`` and no configured
    ``prune_horizon_days``, auto-prune is disabled and nothing is deleted.
    """
    db = TaskStateDB()
    config = LocalConfig.load()
    days = _resolve_prune_days(args, config)
    if days is None:
        print(
            "Auto-prune disabled: pass --older-than <days> or set "
            "prune_horizon_days (0 = off)."
        )
        return
    plan = plan_prune(db, config, older_than_days=days)
    horizon = f"older than {days} day(s) (before {plan.cutoff.isoformat()})"
    if args.dry_run:
        print(
            f"Would prune {plan.delete_count} event(s) {horizon}; "
            f"kept {plan.kept_session_count} protected session(s); "
            f"would retire {len(plan.retire_keys)} upload mapping(s)."
        )
        return
    result = execute_prune(db, plan)
    print(
        f"Pruned {result['deleted']} event(s) {horizon}; "
        f"kept {result['kept_sessions']} protected session(s); "
        f"retired {result['retired']} upload mapping(s)."
    )


def _parse_reap_threshold(value: str) -> float:
    """Parse a ``reap --older-than`` duration into hours (argparse type).

    Contract: a bare number is **hours** (``36`` → 36h), an ``h`` suffix is hours
    (``36h``), and a ``d`` suffix is days (``2d`` → 48h). The value must be
    strictly positive — ``0`` (or negative) would set the staleness cutoff to now
    or the future and reap every active run, so it is rejected as a usage error
    (exit 2) rather than silently wiping the tracker's active runs.

    :raises argparse.ArgumentTypeError: When ``value`` is not a positive
        ``<number>``/``<number>h``/``<number>d`` duration.
    """
    text = value.strip().lower()
    unit_hours = 1.0
    if text.endswith("h"):
        text = text[:-1]
    elif text.endswith("d"):
        unit_hours = 24.0
        text = text[:-1]
    try:
        magnitude = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid --older-than duration: {value!r} "
            "(use e.g. 12h, 36h, 2d, or a plain number of hours)"
        ) from exc
    # Reject non-finite magnitudes (``inf``/``nan`` parse as floats but slip past
    # a bare ``<= 0`` check, then overflow ``timedelta`` in threshold_from_hours).
    if not math.isfinite(magnitude) or magnitude <= 0:
        raise argparse.ArgumentTypeError(
            f"--older-than must be a positive, finite duration, got {value!r}"
        )
    return magnitude * unit_hours


def _reap_run_line(run, *, anchor_closed: bool) -> str:
    """Format the per-run line printed as a stale run is reaped."""
    anchor = "closed" if anchor_closed else "left untouched"
    return (
        f"Reaped run {run.id} ({run.task_name!r}, task {run.task_id}); "
        f"anchor {anchor}."
    )


def cmd_reap(args: argparse.Namespace, client, db: TaskStateDB) -> None:
    """Bulk-abort every stale run in the central tracker DB (#366).

    Finds the active (``RUNNING`` / ``AWAITING_ANSWERS``) runs whose last activity
    predates ``--older-than`` (default 12h) and, unless ``--dry-run``, aborts each
    through the same local-abort path a single ``abort`` uses — stamping
    ``aborted_at`` (so the run is excluded from billing) and best-effort closing
    its unedited Odoo anchor. Idempotent: a second reap finds no stale active runs
    because the first left them ``STOPPED``.
    """
    threshold = threshold_from_hours(args.older_than)
    stale = stale_active_runs(db, threshold)
    if not stale:
        print("No stale runs to reap.")
        return
    if args.dry_run:
        print(f"Would reap {len(stale)} stale run(s):")
        for run in stale:
            print(f"  [{run.id}] {run.task_name!r} (task {run.task_id}, "
                  f"{run.state.value})")
        return
    for run in stale:
        anchor_closed = reap_run(db, client, run)
        print(_reap_run_line(run, anchor_closed=anchor_closed))
    print(f"Reaped {len(stale)} stale run(s).")


def cmd_abort(registry: Registry, args: argparse.Namespace) -> None:
    """Abort a stale run in the central tracker DB and close its Odoo anchor.

    Dispatches through the shared registry's ``abort_run`` command rather than
    constructing :class:`AbortRunCommand` by hand.
    """
    result = registry["abort_run"].execute(args.run_id)
    if result["already_stopped"]:
        print(
            f"Run {result['run_id']} is already stopped; nothing to abort."
        )
        return
    anchor = "closed" if result["anchor_closed"] else "left untouched"
    print(
        f"Aborted run {result['run_id']} ({result['task_name']!r}); "
        f"anchor {anchor}."
    )


def cmd_close(args: argparse.Namespace) -> None:
    """Close a task's tracking run into the terminal CLOSED state (#504).

    Local-only: dispatches through the shared registry's ``close_task`` command (a
    purely local tracker transition that needs no Odoo connection). CLOSED is
    deliberately absent from the MCP tool surface — the ``close_task`` builtin has
    no tool factory — so this CLI ``close`` subcommand is the only way to reach it,
    which is the point (the agent must not see or reason about the state).
    """
    db = TaskStateDB()
    registry = _build_registry(_LazyOdooClient(), state=db)
    result = registry["close_task"].execute(args.task_id)
    if not result["closed"]:
        print(f"No open run to close for task {args.task_id}.")
        return
    print(
        f"Closed run {result['run_id']} ({result['task_name']!r}, "
        f"task {result['task_id']}); state now {result['state']}."
    )


def cmd_get_employee_id(registry: Registry, _args: argparse.Namespace) -> None:
    """Print the ``hr.employee`` id for the authenticated Odoo user (#499).

    Dispatches through the shared registry's ``get_employee_id`` command — the
    same resolver the unattended export path uses — so the CLI and that path agree
    on "who is this?". Needs an Odoo connection to resolve the uid on a cold cache.
    """
    employee_id = registry["get_employee_id"].execute()
    print(f"employee_id={employee_id}")


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
        "--branch",
        default=None,
        help="Session branch (e.g. '<task-id>#<slug>'). Recorded as the event's "
        "provenance and, via the '<task-id>#' convention, used to recover the "
        "task attribution when no --task-id and no active run name it (#574). "
        "Default: resolve from the cwd checkout for provenance only.",
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
        "discover", help="List active runs in the central tracker DB"
    )
    discover_p.add_argument(
        "--stale-after-hours",
        type=float,
        default=12.0,
        dest="stale_after_hours",
        help="Flag active runs started before this many hours ago (default: 12)",
    )

    abort_p = subparsers.add_parser(
        "abort", help="Abort a stale run in the central tracker DB"
    )
    abort_p.add_argument(
        "run_id", type=int, help="Run id (or task id) from 'discover' to abort"
    )

    close_p = subparsers.add_parser(
        "close", help="Close a task's run into the terminal CLOSED state"
    )
    close_p.add_argument(
        "task_id", type=int, help="Odoo task id whose tracking run to close"
    )

    subparsers.add_parser(
        "get-employee-id", help="Print the hr.employee id for the current user"
    )

    reap_p = subparsers.add_parser(
        "reap", help="Bulk-abort every stale run in the central tracker DB"
    )
    reap_p.add_argument(
        "--older-than",
        type=_parse_reap_threshold,
        default=DEFAULT_REAP_THRESHOLD_HOURS,
        dest="older_than",
        help="Staleness horizon: reap runs last active before this ago "
        "(e.g. 12h, 36h, 2d, or a plain number of hours; default: 12h)",
    )
    reap_p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="List the stale runs that would be reaped without aborting anything",
    )

    resync_p = subparsers.add_parser(
        "resync", help="Reconcile local events against git/GitHub/Odoo chatter"
    )
    resync_p.add_argument(
        "--sources",
        default="git,github,odoo",
        help=(
            "Comma-separated subset of git,github,odoo,gcal,gmail "
            "(default: git,github,odoo; gcal/gmail are opt-in Google sources)"
        ),
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

    prune_p = subparsers.add_parser(
        "prune", help="Delete aged hook events past a retention horizon"
    )
    prune_p.add_argument(
        "--older-than",
        type=_positive_int,
        default=None,
        dest="older_than",
        help="Prune events strictly older than this many days (positive integer; "
        "default: the configured prune_horizon_days, else no-op)",
    )
    prune_p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Preview what would be pruned without deleting anything",
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
# ``needs_odoo=False`` command skips the devcontainer assert and builds no
# OdooClient because it touches only local tracker state — ``log-event`` writes
# it, ``discover`` / ``prune`` read it, and ``resync`` is local-first (its
# git/github pullers never touch Odoo and it constructs an OdooClient lazily,
# behind its own env assert, only when ``odoo`` is requested). Adding a
# subcommand means one entry here (plus its parser).
_COMMANDS: dict[str, tuple[Callable[[_Ctx], None], bool]] = {
    "list": (lambda c: cmd_list(c.registry, c.args), True),
    "stop": (lambda c: cmd_stop(c.registry, c.args), True),
    "stop-all": (lambda c: cmd_stop_all(c.registry, c.args), True),
    "report": (lambda c: cmd_report(c.registry, c.args), True),
    "normalize": (lambda c: cmd_normalize(c.registry, c.args), True),
    "abort": (lambda c: cmd_abort(c.registry, c.args), True),
    "get-employee-id": (lambda c: cmd_get_employee_id(c.registry, c.args), True),
    "reap": (lambda c: cmd_reap(c.args, c.client, c.db), True),
    "upload": (lambda c: cmd_upload(c.args, c.registry, c.client, c.db, c.config), True),
    "close": (lambda c: cmd_close(c.args), False),
    "discover": (lambda c: cmd_discover(c.args), False),
    "resync": (lambda c: cmd_resync(c.args), False),
    "prune": (lambda c: cmd_prune(c.args), False),
    "log-event": (lambda c: cmd_log_event(c.args), False),
}

# The local-only command names, derived from the table so routing stays a single
# source of truth.
_LOCAL_ONLY = {name for name, (_, needs_odoo) in _COMMANDS.items() if not needs_odoo}


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.command is None:
        args.command = "list"
    entry = _COMMANDS.get(args.command)
    if entry is None:
        parser.print_help()
        sys.exit(1)
    handler, needs_odoo = entry
    try:
        if not needs_odoo:
            handler(_Ctx(args, None, None, None, None))
            return
        _assert_env()
        client = _LazyOdooClient()
        db = TaskStateDB()
        config = LocalConfig.load()
        registry = _build_registry(client, state=db, config=config)
        handler(_Ctx(args, registry, client, db, config))
    except TrackerStateMissingError as exc:
        # The central DB is host-provisioned and bind-mounted; the SDK never
        # creates it (#369). Surface the single actionable error and fail hard
        # (exit 1) rather than exiting 0 silently. The claude-event-hook
        # backgrounds this CLI and swallows output by contract, so this loud
        # failure is for interactive invocations.
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
