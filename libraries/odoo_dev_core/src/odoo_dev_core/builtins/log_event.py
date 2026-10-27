"""Append one row to the local ``events`` timeseries.

This is the single owner of the ``events`` append write. Two frontends share it:

* the CLI ``log-event`` subcommand, which persists the ``claude:<hook>`` shim
  events, and
* the dispatch telemetry, which records one ``source="agent"`` row per
  successful tool call.

The handler owns the :class:`~odoo_dev_data.events.EventRecord` construction and
the two policies every frontend used to re-implement: task attribution
(:func:`~odoo_dev_core.attribution.resolve_task_ids`) and provenance (``repo`` /
``branch`` resolved from the working tree unless the caller states them).
It performs no Odoo RPC. It is CLI-only: event emission is never an
LLM-callable tool.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Optional

from odoo_dev_data.events import EventRecord

from ..attribution import current_branch_label, resolve_task_ids
from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    source: str
    subject: str = ""
    payload: Optional[dict[str, Any]] = None
    task_ids: Optional[Iterable[Any]] = None
    repo: Optional[str] = None
    branch: Optional[str] = None
    pr_num: int = 0
    external_id: Optional[str] = None
    timestamp: Optional[datetime] = None
    attach_active_run: bool = True


@command(
    "log_event",
    args=Args,
    description=(
        "Append one row to the local events timeseries. Records the source, "
        "subject, payload, task scope, and repo/branch/PR provenance of a single "
        "hook/dispatch event; performs no Odoo RPC."
    ),
    surfaces=frozenset({"cli"}),
)
def handle(services: Services, args: Args) -> dict[str, Any]:
    """Persist one event row and return a summary of what was written.

    ``source``, ``subject``, ``payload``, ``pr_num``, and ``external_id`` are
    written verbatim. The task scope is resolved by ``resolve_task_ids``,
    ``repo`` and ``branch`` come from the working tree when the caller leaves
    them ``None`` (``""`` records none), and ``timestamp`` defaults to the
    clock's current time. A stated ``<task-id>-<slug>`` ``branch`` recovers the
    task attribution when nothing else names it (#574).

    :return: ``{"source", "subject", "task_ids"}`` describing the written row.
    """
    # Provenance for the column is best-effort from the working tree when the
    # caller leaves it unstated; attribution recovery consults only the branch
    # the caller explicitly states, so ``args.branch`` is what is handed to it.
    resolved_branch = current_branch_label() if args.branch is None else args.branch
    record = EventRecord(
        id=None,
        source=args.source,
        timestamp=args.timestamp or services.clock.now(),
        task_ids=resolve_task_ids(args.task_ids, args.attach_active_run, args.branch),
        repo=services.git.current_repo_label() if args.repo is None else args.repo,
        branch=resolved_branch,
        pr_num=args.pr_num,
        subject=args.subject,
        payload=args.payload,
        external_id=args.external_id,
    )
    services.events.add_event(record)
    return {
        "source": record.source,
        "subject": record.subject,
        "task_ids": record.task_ids,
    }
