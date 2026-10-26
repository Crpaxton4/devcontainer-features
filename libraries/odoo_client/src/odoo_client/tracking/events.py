"""Core façade over the persisted event stream's sessionization read path (#718).

The one core-owned door to :func:`odoo_client.adapters.state.load_raw_events`
(the adapter bridging stored :class:`~odoo_client.tracking.models.EventRecord`
rows to the pure sessionization vocabulary). Under ADR-005 rule 4 a surface
must reach the data-layer adapters through core; the TUI export renderers
and the ``optimize_sessions`` command both read raw events through this
module, mirroring the door :mod:`odoo_client.commands.log_event` provides for
the event-source vocabulary (#717).
"""

from odoo_client.adapters.state import load_raw_events

__all__ = ["load_raw_events"]
