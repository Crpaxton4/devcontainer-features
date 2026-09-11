"""State-persistence adapter package (#718): the ``StateStore`` port's data side.

One package per external system (ADR-005 amendment, #718). The "external
system" here is the local SQLite events store: :mod:`.persistence` bridges
persisted :class:`~odoo_sdk.tracking.models.EventRecord` rows to the pure
sessionization vocabulary. The concrete store itself
(:class:`~odoo_sdk.state.LocalStateClient`) satisfies the core-owned
:class:`~odoo_sdk.commands.protocols.StateStore` Protocol structurally; core
reaches this read path through :mod:`odoo_sdk.tracking.events`.
"""

from .persistence import (
    UnknownEventSourceError,
    event_record_to_raw_event,
    is_synthetic_tick,
    load_raw_events,
    raw_event_to_event_record,
    source_to_event_type,
)

__all__ = [
    "UnknownEventSourceError",
    "event_record_to_raw_event",
    "is_synthetic_tick",
    "load_raw_events",
    "raw_event_to_event_record",
    "source_to_event_type",
]
