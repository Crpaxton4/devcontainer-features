"""Shared-kernel error façade for internal (and external) consumers.

The canonical error definitions live in the data layer: the Odoo error
taxonomy in :mod:`odoo_client.transport.errors`. Higher layers used to reach them through
``from odoo_client import ...`` root-imports, which made module import order
depend on the partially initialized :mod:`odoo_client` package (a cycle that was
only survivable because of a lazy import in
:meth:`odoo_client.commands.log_event.LogEventCommand`).

This module is the shared-kernel alternative (ADR-005): it re-exports the
error types from their canonical modules so that any layer — core, surface,
or shared helpers — can name them without importing the package root and
without importing the defining data modules directly. It deliberately
contains no logic and no classes of its own; every symbol here *is* the
canonical object.

Internal code must import error types from here (or from the canonical
module); ``from odoo_client import ...`` root-imports inside ``src/odoo_client``
are rejected by the static-analysis gate in ``tools/static_analysis.py``.
"""

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
    # Odoo error taxonomy (canonical: odoo_client.transport.errors)
    "OdooError",
    "OdooAuthenticationError",
    "OdooAccessError",
    "OdooValidationError",
    "OdooMissingRecordError",
    "OdooTransportError",
    "OdooServerError",
    "DeletionNotSupportedError",
]
