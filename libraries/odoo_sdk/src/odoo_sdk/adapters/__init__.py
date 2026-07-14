"""Adapters that connect the pure sessionization core to I/O boundaries.

The :mod:`odoo_sdk.sessionization` package is pure and state-agnostic. These
adapters translate between its data structures and the SDK's stateful edges
(the SQLite ``events`` table). All coupling to persistence and external systems
lives here, never in the pure core.
"""

from .external_sync import sync_git_log, sync_github, sync_odoo_chatter
from .state_persistence import (
    UnknownEventSourceError,
    event_record_to_raw_event,
    load_raw_events,
    raw_event_to_event_record,
    source_to_event_type,
)

__all__ = [
    "event_record_to_raw_event",
    "raw_event_to_event_record",
    "source_to_event_type",
    "UnknownEventSourceError",
    "load_raw_events",
    "sync_git_log",
    "sync_github",
    "sync_odoo_chatter",
]
