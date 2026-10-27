"""Reconcile local event state against external history.

A thin handler over :func:`odoo_dev_core.workflows.resync.run_resync`, which
owns the dispatch to the git, GitHub and Odoo-chatter pullers. The surface is
directory-agnostic: the ``git`` puller recursively discovers every checkout under
the current directory and the ``github`` puller searches the authenticated
account across all repos. Sessions are derived from events at query time, so a
resync needs only to write events.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from ..ports import Services
from ..registry import command
from ..workflows.resync import parse_sources, run_resync


@dataclass(frozen=True)
class Args:
    sources: str = "git,github,odoo"
    start: Optional[str] = None
    end: Optional[str] = None


def _parse_range_date(value: Optional[str]) -> Optional[date]:
    """Parse one optional inclusive ISO date bound.

    :raises ValueError: When ``value`` is present but not a valid ISO date, so a
        malformed range fails loudly before any puller runs.
    """
    return date.fromisoformat(value) if value else None


@command(
    "resync",
    args=Args,
    description=(
        "Reconcile local event state against external history: pull authored "
        "git commits from every repository under the current directory (any "
        "depth), the authenticated user's account-wide GitHub PRs, reviews, and "
        "comments, and their Odoo task chatter into the local events table. "
        "Manual and idempotent (dedup by external id). A source whose tooling "
        "is entirely unusable reports an 'error' entry; an optional absent "
        "source reports 'skipped'. Optional inclusive 'start'/'end' ISO dates "
        "bound the capture window (default: the rolling "
        "resync_window_days). Pass a comma-separated 'sources' subset of "
        "git,github,odoo (default: git,github,odoo)."
    ),
)
def handle(services: Services, args: Args) -> dict[str, Any]:
    """Run the requested pullers and return a per-source summary.

    :param sources: Comma-separated subset of ``git,github,odoo``;
        blank or unrecognized-only input runs the default git/github/odoo.
    :param start: Optional inclusive ISO start date (``YYYY-MM-DD``) for
        the capture window.
    :param end: Optional inclusive ISO end date, same scope as ``start``.
    :return: Mapping of each run source to its puller summary dict
        (``{"inserted": n, ...}``, ``{"skipped": reason}``, or
        ``{"error": reason}``).
    :raises ValueError: When ``start`` or ``end`` is not a valid ISO date.
    """
    start = _parse_range_date(args.start)
    end = _parse_range_date(args.end)
    report = run_resync(
        services, sources=parse_sources(args.sources), start=start, end=end
    )
    return report.results
