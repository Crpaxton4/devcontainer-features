"""Reconcile local event state against external history.

``resync`` is a manual reconciliation utility (no background trigger): it runs
the idempotent git, GitHub and Odoo-chatter pullers and writes any missing
events. The surface is directory-agnostic: the ``git`` puller recursively
discovers every checkout under the current directory and the ``github`` puller
searches the authenticated account across all repos. Sessions are derived from
events at query time, so a resync needs only to write events.

The ``git`` and ``github`` pullers are purely local and never touch Odoo; only
the ``odoo`` puller uses the client. Each puller is idempotent. A source whose
tooling is entirely unusable reports an ``error`` entry (callers decide whether
that is fatal); a genuinely optional absence reports ``skipped``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from ..ports import Services
from ..registry import command

_DEFAULT_SOURCES = ("git", "github", "odoo")


@dataclass(frozen=True)
class Args:
    sources: str = "git,github,odoo"
    start: Optional[str] = None
    end: Optional[str] = None


def _parse_sources(sources: str) -> list[str]:
    """Return the requested pullers from a comma-separated ``sources`` string.

    Order follows ``_DEFAULT_SOURCES`` (not the input order) so the result is
    stable, and unknown tokens are ignored. An empty/blank string selects the
    default sources (git/github/odoo).
    """
    requested = {token.strip() for token in sources.split(",") if token.strip()}
    if not requested:
        return list(_DEFAULT_SOURCES)
    return [source for source in _DEFAULT_SOURCES if source in requested]


def _parse_range_date(value: Optional[str]) -> Optional[date]:
    """Parse one optional inclusive ISO date bound.

    :raises ValueError: When ``value`` is present but not a valid ISO date, so a
        malformed range fails loudly before any puller runs.
    """
    return date.fromisoformat(value) if value else None


def _run_puller(
    services: Services, source: str, start: Optional[date], end: Optional[date]
) -> dict[str, Any]:
    """Run one source's puller against the shared ports over ``[start, end]``."""
    client, events, config = services.client, services.events, services.config
    if source == "git":
        return services.git.sync_git_log(events, config, client, start=start, end=end)
    if source == "github":
        return services.github.sync_github(events, config, client, start=start, end=end)
    return services.chatter.sync_odoo_chatter(
        client, events, config, start=start, end=end
    )


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
    return {
        source: _run_puller(services, source, start, end)
        for source in _parse_sources(args.sources)
    }
