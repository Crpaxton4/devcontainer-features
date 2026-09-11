"""Shared-kernel error façade for internal (and external) consumers.

The canonical error definitions live in the data layer: the Odoo error
taxonomy in :mod:`odoo_sdk.transport.errors` and the task-tracker FSM errors
in :mod:`odoo_sdk.tracking.models` (promoted from ``state.models`` by #718;
the old path remains a sanctioned alias). Higher layers used to reach them through
``from odoo_sdk import ...`` root-imports, which made module import order
depend on the partially initialized :mod:`odoo_sdk` package (a cycle that was
only survivable because of a lazy import in
:meth:`odoo_sdk.commands.log_event.LogEventCommand`).

This module is the shared-kernel alternative (ADR-005): it re-exports the
error types from their canonical modules so that any layer — core, surface,
or shared helpers — can name them without importing the package root and
without importing the defining data modules directly. It deliberately
contains no logic and no classes of its own; every symbol here *is* the
canonical object.

Internal code must import error types from here (or from the canonical
module); ``from odoo_sdk import ...`` root-imports inside ``src/odoo_sdk``
are rejected by the static-analysis gate in ``tools/static_analysis.py``.
"""

# Deliberately imported through the sanctioned ``state.models`` alias rather
# than the canonical ``tracking.models`` (#718): the FSM classes are the
# identical objects either way (the alias IS the relocated module), but the
# alias edge is the one place ADR-005 already permits a data path to reach
# the promoted vocabulary — importing the core module here directly would
# thread a new data→core chain through every data-layer consumer of this
# façade (services -> errors -> tracking), which rules 3/6 rightly reject.
from .state.models import (
    InvalidStateTransitionError,
    TaskAlreadyRunningError,
    TaskNotRunningError,
    TrackerStateMissingError,
)
from .transport.errors import (
    DeletionNotSupportedError,
    OdooAccessError,
    OdooAuthenticationError,
    OdooError,
    OdooMissingRecordError,
    OdooServerError,
    OdooTransportError,
    OdooValidationError,
)

__all__ = [
    # Odoo error taxonomy (canonical: odoo_sdk.transport.errors)
    "OdooError",
    "OdooAuthenticationError",
    "OdooAccessError",
    "OdooValidationError",
    "OdooMissingRecordError",
    "OdooTransportError",
    "OdooServerError",
    "DeletionNotSupportedError",
    # Task-tracker FSM errors (canonical: odoo_sdk.tracking.models, #718)
    "TrackerStateMissingError",
    "TaskAlreadyRunningError",
    "TaskNotRunningError",
    "InvalidStateTransitionError",
]
