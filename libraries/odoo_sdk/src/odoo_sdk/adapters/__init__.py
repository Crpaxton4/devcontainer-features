"""Adapters that connect the pure sessionization core to I/O boundaries.

The :mod:`odoo_sdk.sessionization` package is pure and state-agnostic. These
adapters translate between its data structures and the SDK's stateful edges
(the SQLite ``events`` / ``sessions`` tables). All coupling to persistence and
external systems lives here, never in the pure core.
"""

from .state_persistence import (
    UnknownEventSourceError,
    event_record_to_raw_event,
    ingest_events_incrementally,
    load_raw_events,
    persist_session_windows,
    raw_event_to_event_record,
    source_to_event_type,
    time_entry_to_session_window,
)

__all__ = [
    "event_record_to_raw_event",
    "raw_event_to_event_record",
    "source_to_event_type",
    "UnknownEventSourceError",
    "time_entry_to_session_window",
    "load_raw_events",
    "persist_session_windows",
    "ingest_events_incrementally",
]
