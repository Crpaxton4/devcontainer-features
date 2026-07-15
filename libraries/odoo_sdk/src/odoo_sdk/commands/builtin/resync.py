"""Built-in command that reconciles local event state against external history.

``resync`` is a manual reconciliation utility (no background trigger): it runs
the idempotent :mod:`odoo_sdk.adapters.external_sync` pullers over the *current*
repo and writes any missing events. Sessions are derived from events at query
time, so a resync needs only to write events — the next ``query_sessions`` call
surfaces the reconciled sessions automatically, with no ingest step.

The ``git`` and ``github`` pullers are purely local/CLI-backed and never touch
Odoo; only the ``odoo`` puller uses the injected client. Each puller is
idempotent and tolerant of its tool being absent, so a resync degrades to
per-source skip notices rather than failing.
"""

from __future__ import annotations

from typing import Any

from odoo_sdk.adapters import (
    sync_git_log,
    sync_github,
    sync_gmail,
    sync_google_calendar,
    sync_odoo_chatter,
)

from ..command import Command
from ._registration import builtin_command

# The pullers a resync can run, in a stable order. ``gcal``/``gmail`` reach the
# Google APIs and require host-provisioned credentials, so they are opt-in: NOT
# in the default source string, only run when explicitly requested (issue #370).
_DEFAULT_SOURCES = ("git", "github", "odoo")
_GOOGLE_SOURCES = ("gcal", "gmail")
_ALL_SOURCES = _DEFAULT_SOURCES + _GOOGLE_SOURCES


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


@builtin_command
class ResyncCommand(Command):
    """Reconcile local event state against git, GitHub, and Odoo chatter.

    Manual-only, current-repo-scoped, and idempotent. Runs the requested pullers
    and returns a per-source summary dict; a second run inserts nothing because
    every event is deduped on its stable external id.
    """

    _name = "resync"
    _description = (
        "Reconcile local event state against external history for the current "
        "repo: pull authored git commits, merged GitHub PRs and reviews, and the "
        "authenticated user's Odoo task chatter into the local events table. "
        "Manual, idempotent (dedup by external id), and tolerant of any source's "
        "tool being absent (that source is skipped). Sessions derive from events "
        "at query time, so no ingest step is needed. Pass a comma-separated "
        "'sources' subset of git,github,odoo,gcal,gmail (default: git,github,odoo). "
        "gcal/gmail are opt-in Google sources needing host-provisioned credentials."
    )

    def execute(self, sources: str = "git,github,odoo") -> dict[str, Any]:
        """Run the requested pullers and return a per-source summary.

        :param sources: Comma-separated subset of ``git,github,odoo,gcal,gmail``;
            blank or unrecognized-only input runs the default git/github/odoo
            (the Google sources are opt-in and never run by default).
        :return: Mapping of each run source to its puller summary dict
            (``{"inserted": n}`` or ``{"skipped": reason}``).
        """
        selected = _parse_sources(sources)
        summary: dict[str, Any] = {}
        if "git" in selected:
            summary["git"] = sync_git_log(self.state, self.config, self._client)
        if "github" in selected:
            summary["github"] = sync_github(self.state, self.config, self._client)
        if "odoo" in selected:
            summary["odoo"] = sync_odoo_chatter(self._client, self.state, self.config)
        if "gcal" in selected:
            summary["gcal"] = sync_google_calendar(self.state, self.config)
        if "gmail" in selected:
            summary["gmail"] = sync_gmail(self.state, self.config)
        return summary
