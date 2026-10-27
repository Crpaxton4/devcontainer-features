"""The resync workflow: reconcile local event state against external history.

``resync`` is a manual reconciliation utility (no background trigger): it runs
the idempotent git, GitHub and Odoo-chatter pullers and writes any missing
events. The ``git`` and ``github`` pullers are purely local and never touch
Odoo. Only the ``odoo`` puller uses the client. A source whose tooling is
entirely unusable reports an ``error`` entry (callers decide whether that is
fatal). A genuinely optional absence reports ``skipped``.

The workflow drives the gateway ports on :class:`~odoo_dev_core.ports.Services`
and imports no adapter, so a test runs it against fakes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Optional, Sequence

from odoo_dev_core.ports import Services

DEFAULT_SOURCES = ("git", "github", "odoo")


def parse_sources(sources: str) -> list[str]:
    """Return the requested pullers from a comma-separated ``sources`` string.

    Order follows ``DEFAULT_SOURCES`` (not the input order) so the result is
    stable, and unknown tokens are ignored. An empty/blank string selects the
    default sources (git/github/odoo).
    """
    requested = {token.strip() for token in sources.split(",") if token.strip()}
    if not requested:
        return list(DEFAULT_SOURCES)
    return [source for source in DEFAULT_SOURCES if source in requested]


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


def format_resync_line(source: str, result: dict) -> str:
    """Format one per-source resync result line.

    ``error`` (tooling entirely unusable, #652) and ``skipped`` (optional source
    absent) render distinctly. A success line carries the per-source coverage
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


@dataclass(frozen=True)
class ResyncReport:
    """The per-source summaries of one resync run."""

    results: dict[str, dict]

    @property
    def ok(self) -> bool:
        """Return whether no source reported an ``error``."""
        return not any("error" in result for result in self.results.values())

    def lines(self) -> list[str]:
        """Return one display line per source, in run order."""
        return [
            format_resync_line(source, result)
            for source, result in self.results.items()
        ]


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


def run_resync(
    services: Services,
    *,
    sources: Sequence[str] = DEFAULT_SOURCES,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> ResyncReport:
    """Run the requested pullers and return their per-source summaries.

    :param sources: The pullers to run. Unknown names are ignored. The run
        order is always git, github, odoo.
    :param start: Optional inclusive start date of the capture window.
    :param end: Optional inclusive end date of the capture window.
    """
    return ResyncReport(
        {
            source: _run_puller(services, source, start, end)
            for source in DEFAULT_SOURCES
            if source in sources
        }
    )
