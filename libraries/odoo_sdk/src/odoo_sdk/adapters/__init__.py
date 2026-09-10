"""Adapters that connect the SDK's core to its I/O boundaries, one package per system.

Netflix-Dispatch-style layout (ADR-005 amendment, #718): each external
system the SDK reconciles against owns one subpackage, and each subpackage
sits behind a core-owned consumer-side Protocol
(:mod:`odoo_sdk.commands.protocols`):

* :mod:`.git` — local git checkouts (``GitGateway``).
* :mod:`.github` — the ``gh``-authenticated GitHub account (``IssueTracker``).
* :mod:`.odoo` — Odoo task chatter, over the existing ``RpcClient`` port.
* :mod:`.google` — Google Calendar + sent Gmail (``CalendarGateway``).
* :mod:`.state` — the local SQLite events store (``StateStore``); the
  persistence bridge to the pure sessionization vocabulary lives here.

All coupling to persistence and external processes lives in these packages,
never in core. The package-level re-exports below are the historical flat
surface and stay stable; :mod:`.external_sync` additionally remains as the
git/GitHub/Odoo implementation substrate and compat re-export module (see
its layout note for why the frozen test seams pin the code there).
"""

from .git import sync_git_log
from .github import sync_github
from .google import GoogleAPIError, GoogleAuthError, sync_gmail, sync_google_calendar
from .odoo import sync_odoo_chatter
from .state import (
    UnknownEventSourceError,
    event_record_to_raw_event,
    is_synthetic_tick,
    load_raw_events,
    raw_event_to_event_record,
    source_to_event_type,
)

__all__ = [
    "event_record_to_raw_event",
    "raw_event_to_event_record",
    "source_to_event_type",
    "is_synthetic_tick",
    "UnknownEventSourceError",
    "load_raw_events",
    "sync_git_log",
    "sync_github",
    "sync_odoo_chatter",
    "sync_google_calendar",
    "sync_gmail",
    "GoogleAuthError",
    "GoogleAPIError",
]
