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
from typing import Callable, Optional

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
from odoo_sdk.commands import LogEventCommand
from odoo_sdk.commands.builtin.abort_run import AbortRunCommand
from odoo_sdk.commands.builtin.query_sessions import QuerySessionsCommand
from odoo_sdk.commands.builtin.stop_task import StopTaskCommand
from odoo_sdk.sessionization import EventType
from odoo_sdk.state import LocalConfig, TrackerStateMissingError
from odoo_sdk.state import LocalStateClient as TaskStateDB
from odoo_sdk.state import TaskState
from odoo_sdk.state.db import current_repo_label
from odoo_sdk.state.discovery import discover_runs
from odoo_sdk.utilities.env import (
    OdooDevcontainerRequiredError,
    assert_odoo_devcontainer,
)
from odoo_sdk.utilities.odoo_helpers import merge_timesheets
from odoo_sdk.utilities.prune import execute_prune, plan_prune, resolve_horizon
from odoo_sdk.utilities.reap import (
    DEFAULT_REAP_THRESHOLD_HOURS,
    is_run_stale,
    reap_run,
    resolve_env_threshold_hours,
    stale_active_runs,
    threshold_from_hours,
)
from odoo_sdk.utilities.upload import upload_sessions

# Subcommands that skip the global Odoo devcontainer assert and build no
# OdooClient up front. ``log-event`` / ``discover`` are purely local; ``resync``
# is local-first — its git/github pullers never touch Odoo, and it constructs an
# OdooClient lazily (behind its own env assert) only when ``odoo`` is requested,
# so the global assert must not gate it. ``prune`` is purely local too: it deletes
# aged local events and retires local ledger mappings, never writing to Odoo (a
# pruned session's billed Odoo row keeps its hours; only the local mapping goes).
_LOCAL_ONLY = {"log-event", "discover", "resync", "prune"}


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
    """Force-stop one active run through the shared :class:`StopTaskCommand`.

    Routing through the command keeps the "upload owns hours" invariant: stopping
    a run only transitions it to STOPPED and records local session data — it never
    writes ``account.analytic.line`` ``unit_amount`` hours. The elapsed wall-clock
    is billed later by the upload/reconcile path (minimum floor + step rounding),
    so the same run bills identically whether stopped here or via the MCP tool.
    """
    run_id: int = args.run_id
    run = db.get_run_by_id(run_id)
    if run is None:
        print(f"Error: no run with id {run_id}.", file=sys.stderr)
        sys.exit(1)
    if run.state == TaskState.STOPPED:
        print(f"Run {run_id} is already stopped.")
        return

    description = "[/] Run ended via CLI force-stop"
    result = StopTaskCommand(client, state=db).execute(run.task_id, description)
    print(
        f"Stopped run {run_id}: {result['task_name']!r}  "
        f"elapsed={result['elapsed']}"
    )


def cmd_stop_all(db: TaskStateDB, _args: argparse.Namespace, client: OdooClient) -> None:
    """Force-stop every active run through the shared :class:`StopTaskCommand`.

    As with :func:`cmd_stop`, each stop only transitions the run to STOPPED and
    never writes timesheet hours; the upload path owns all ``unit_amount`` writes.
    """
    runs = db.get_all_active_runs()
    if not runs:
        print("Nothing to stop.")
        return
    description = "[/] Run ended via CLI force-stop"
    command = StopTaskCommand(client, state=db)
    for r in runs:
        command.execute(r.task_id, description)
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
    """Bind to the central tracker DB (construction is inert; no I/O yet).

    The central DB is host-provisioned and bind-mounted (#369): binding never
    touches the filesystem, so a missing DB is not surfaced here but on the first
    query/write as a :class:`TrackerStateMissingError`, which :func:`main`
    renders as one clean, actionable error. Unlike the pre-#369 behavior there is
    no silent exit-0 for a non-git cwd — repo identity no longer selects the DB,
    so events log fine with ``repo=""`` from anywhere.
    """
    return TaskStateDB()


def _resolve_task_ids(db: TaskStateDB, args: argparse.Namespace) -> list[str]:
    """Resolve which task ids a logged event attributes to.

    An explicit ``--task-id`` (repeatable) always wins. Otherwise, when
    ``--attach-active-run`` is passed, attach every active (RUNNING /
    AWAITING_ANSWERS) run in the central tracker DB — the natural association for
    a hook firing while an FSM run is in progress. With no active run (or the
    flag absent) this yields ``[]``, i.e. untargeted session-level activity,
    matching the pre-flag default.

    Stale runs are excluded (#366): a run whose last activity predates the reap
    threshold (``ODOO_REAP_THRESHOLD_HOURS`` env, default
    :data:`~odoo_sdk.utilities.reap.DEFAULT_REAP_THRESHOLD_HOURS`) is a wedged
    orphan from a dead devcontainer, so attaching this event to it would only
    accrue phantom billable wall-clock. Skipping it freezes its activity clock so
    it stays reapable. The same staleness predicate ``reap`` uses is applied here,
    so the two agree on exactly which runs are stale.
    """
    if args.task_id:
        return [str(task_id) for task_id in args.task_id]
    if args.attach_active_run:
        threshold = threshold_from_hours(resolve_env_threshold_hours())
        return [
            str(run.task_id)
            for run in db.get_all_active_runs()
            if not is_run_stale(db, run, threshold)
        ]
    return []


def cmd_log_event(args: argparse.Namespace) -> None:
    """Record a single Claude Code hook event into the local ``events`` table.

    The event write is routed through :class:`~odoo_sdk.commands.log_event.
    LogEventCommand` — the single command-layer owner of the ``events``
    append (issue #407) — so this subcommand no longer constructs an
    ``EventRecord`` and calls ``add_event`` inline. The interface-specific
    resolution stays here: ``--source`` validation, ``--payload`` parsing, the
    ``--task-id`` / ``--attach-active-run`` task-scope policy, and the repo label
    are computed from ``args`` and handed to the command.
    """
    event_type = _resolve_source(args.source)
    payload = _parse_payload(args.payload)
    db = _open_local_db()
    LogEventCommand(state=db).execute(
        source=args.source,
        subject=args.subject,
        payload=payload,
        task_ids=_resolve_task_ids(db, args),
        repo=current_repo_label(),
        timestamp=args.timestamp,
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
    """List every active run in the central tracker DB."""
    runs = discover_runs(stale_after_hours=args.stale_after_hours)
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
    db = _open_local_db()
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


def cmd_upload(args: argparse.Namespace, db: TaskStateDB, client: OdooClient) -> None:
    """Bill derived sessions headlessly, sharing the TUI's upload path (#354).

    Runs the same pipeline the TUI's ``u`` key does — ``query_sessions`` derives
    the sessions for the optional date range, then the shared
    :func:`~odoo_sdk.utilities.upload.upload_sessions` loop reconciles each
    session's hours and sweeps the window's stale upload mappings — so a
    non-interactive ``odoo-sdk upload`` bills exactly the rows the TUI would.
    ``--dry-run`` previews the billable set without writing to Odoo.
    """
    config = LocalConfig.load()
    sessions = QuerySessionsCommand(client, state=db, config=config).execute(
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

    Local-only: plans the prune (see :func:`~odoo_sdk.utilities.prune.plan_prune`)
    so that no un-uploaded session's events and no still-tracked session's
    minimum-id key can ever be disturbed, then either previews (``--dry-run``) or
    executes the deletion and retires the ledger mappings of the fully-uploaded,
    fully-aged sessions it removed. With no ``--older-than`` and no configured
    ``prune_horizon_days``, auto-prune is disabled and nothing is deleted.
    """
    db = _open_local_db()
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


def cmd_reap(args: argparse.Namespace, client: OdooClient) -> None:
    """Bulk-abort every stale run in the central tracker DB (#366).

    Finds the active (``RUNNING`` / ``AWAITING_ANSWERS``) runs whose last activity
    predates ``--older-than`` (default 12h) and, unless ``--dry-run``, aborts each
    through the same local-abort path a single ``abort`` uses — stamping
    ``aborted_at`` (so the run is excluded from billing) and best-effort closing
    its unedited Odoo anchor. Idempotent: a second reap finds no stale active runs
    because the first left them ``STOPPED``.
    """
    db = _open_local_db()
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


def cmd_abort(args: argparse.Namespace, client: OdooClient) -> None:
    """Abort a stale run in the central tracker DB and close its Odoo anchor."""
    result = AbortRunCommand(client).execute(args.run_id)
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
    elif args.command == "reap":
        cmd_reap(args, OdooClient())
    elif args.command == "upload":
        cmd_upload(args, db, OdooClient())
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
    elif args.command == "prune":
        cmd_prune(args)
    else:
        cmd_log_event(args)


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.command is None:
        args.command = "list"
    try:
        if args.command in _LOCAL_ONLY:
            _dispatch_local_only(args)
            return
        _assert_env()
        _dispatch(parser, TaskStateDB(), args)
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
