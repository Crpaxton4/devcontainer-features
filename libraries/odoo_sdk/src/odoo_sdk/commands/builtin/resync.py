"""Built-in command that reconciles local event state against external history.

``resync`` is a manual reconciliation utility (no background trigger): it runs
the idempotent :mod:`odoo_sdk.adapters.external_sync` pullers and writes any
missing events. Since #652 the surface is directory-agnostic: the ``git``
puller recursively discovers every checkout under the current directory and the
``github`` puller searches the authenticated account across all repos, so the
same activity is captured wherever resync runs. Sessions are derived from
events at query time, so a resync needs only to write events — the next
``query_sessions`` call surfaces the reconciled sessions automatically, with no
ingest step.

The ``git`` and ``github`` pullers are purely local/CLI-backed and never touch
Odoo; only the ``odoo`` puller uses the injected client. Each puller is
idempotent. A source whose tooling is entirely unusable reports an ``error``
entry (callers decide whether that is fatal — the CLI exits nonzero); a
genuinely optional absence still reports ``skipped``.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from odoo_sdk.adapters import (
    GoogleAPIError,
    GoogleAuthError,
    sync_git_log,
    sync_github,
    sync_gmail,
    sync_google_calendar,
    sync_odoo_chatter,
)

from ..command import Command
from ._registration import builtin_command

# Note appended to a Google source's summary when an explicit range was given:
# the Google pullers have no start/end parameters, so silently sweeping their
# own rolling window while the user believes a backfill ran would be a trap.
_RANGE_IGNORED_NOTE = (
    "start/end ignored: this source always sweeps its google_sync_window_days "
    "window"
)


def _run_google(puller, cmd, ranged: bool) -> dict[str, Any]:
    """Run one Google puller behind the same guard the CLI applies.

    The pullers raise ``GoogleAuthError``/``GoogleAPIError`` (and ``ValueError``
    for a rejected tick interval) rather than silently ingesting nothing; at
    this shared command surface (TUI/MCP) those must degrade to a per-source
    skip — matching the CLI's ``_resync_google`` — instead of escaping as
    unhandled exceptions. ``ranged`` appends the range-ignored note so an
    explicit ``start``/``end`` is never silently discarded.
    """
    try:
        result = puller(cmd.state, cmd.config)
    except (GoogleAuthError, GoogleAPIError, ValueError) as exc:
        return {"skipped": str(exc)}
    if ranged:
        result = {**result, "note": _RANGE_IGNORED_NOTE}
    return result


# The pullers a resync can run, keyed by source in a stable order; each value
# runs that source's sync against the command's shared dependencies plus the
# optional explicit date range. git/github/odoo honor ``start``/``end``; the
# Google sources ignore them (they keep their own ``google_sync_window_days``
# window and annotate their summary when a range was requested). ``gcal`` and
# ``gmail`` reach the Google APIs and require host-provisioned credentials, so
# they are opt-in: NOT in the default source string, only run when explicitly
# requested (issue #370).
_SYNC_DISPATCH = {
    "git": lambda cmd, start, end: sync_git_log(
        cmd.state, cmd.config, cmd._client, start=start, end=end
    ),
    "github": lambda cmd, start, end: sync_github(
        cmd.state, cmd.config, cmd._client, start=start, end=end
    ),
    "odoo": lambda cmd, start, end: sync_odoo_chatter(
        cmd._client, cmd.state, cmd.config, start=start, end=end
    ),
    "gcal": lambda cmd, start, end: _run_google(
        sync_google_calendar, cmd, bool(start or end)
    ),
    "gmail": lambda cmd, start, end: _run_google(sync_gmail, cmd, bool(start or end)),
}
_DEFAULT_SOURCES = ("git", "github", "odoo")
_ALL_SOURCES = tuple(_SYNC_DISPATCH)


def _parse_sources(sources: str) -> list[str]:
    """Return the requested pullers from a comma-separated ``sources`` string.

    Order follows :data:`_ALL_SOURCES` (not the input order) so the result is
    stable, and unknown tokens are ignored. An empty/blank string selects the
    DEFAULT sources only (git/github/odoo); the Google sources are opt-in and
    must be named explicitly, so ``resync`` never reaches the network by default.
    """
    requested = {token.strip() for token in sources.split(",") if token.strip()}
    if not requested:
        return list(_DEFAULT_SOURCES)
    return [source for source in _ALL_SOURCES if source in requested]


def _parse_range_date(value: Optional[str]) -> Optional[date]:
    """Parse one optional inclusive ISO date bound.

    :raises ValueError: When ``value`` is present but not a valid ISO date, so a
        malformed range fails loudly before any puller runs.
    """
    return date.fromisoformat(value) if value else None


@builtin_command
class ResyncCommand(Command):
    """Reconcile local event state against git, GitHub, and Odoo chatter.

    Manual-only, directory-agnostic (#652), and idempotent. Runs the requested
    pullers over the resolved date window and returns a per-source summary
    dict; a second run inserts nothing because every event is deduped on its
    stable external id.
    """

    _name = "resync"
    _description = (
        "Reconcile local event state against external history: pull authored "
        "git commits from every repository under the current directory (any "
        "depth), the authenticated user's account-wide GitHub PRs, reviews, and "
        "comments, and their Odoo task chatter into the local events table. "
        "Manual and idempotent (dedup by external id). A source whose tooling "
        "is entirely unusable reports an 'error' entry; an optional absent "
        "source reports 'skipped'. Optional inclusive 'start'/'end' ISO dates "
        "bound the git/github/odoo capture window (default: the rolling "
        "resync_window_days). Pass a comma-separated 'sources' subset of "
        "git,github,odoo,gcal,gmail (default: git,github,odoo). gcal/gmail are "
        "opt-in Google sources needing host-provisioned credentials and keep "
        "their own google_sync_window_days window."
    )

    def execute(
        self,
        sources: str = "git,github,odoo",
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> dict[str, Any]:
        """Run the requested pullers and return a per-source summary.

        :param sources: Comma-separated subset of ``git,github,odoo,gcal,gmail``;
            blank or unrecognized-only input runs the default git/github/odoo
            (the Google sources are opt-in and never run by default).
        :param start: Optional inclusive ISO start date (``YYYY-MM-DD``) for
            the git/github/odoo capture window; gcal/gmail ignore it.
        :param end: Optional inclusive ISO end date, same scope as ``start``.
        :return: Mapping of each run source to its puller summary dict
            (``{"inserted": n, ...}``, ``{"skipped": reason}``, or
            ``{"error": reason}``).
        :raises ValueError: When ``start`` or ``end`` is not a valid ISO date.
        """
        start_date = _parse_range_date(start)
        end_date = _parse_range_date(end)
        selected = _parse_sources(sources)
        return {
            source: _SYNC_DISPATCH[source](self, start_date, end_date)
            for source in selected
        }
