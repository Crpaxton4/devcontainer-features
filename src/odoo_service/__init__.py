"""Public Odoo service exports.

Phase A preserves the current service exports as the supported public
surface. `OdooEnv`, `DomainExpression`, and `OdooRecordset` are part of
the internal Phase A architecture, but they remain intentionally absent
from `__all__` until a later phase widens the supported API.
"""

from .odoo_client import OdooClient
from .odoo_config import OdooConnectionSettings
from .odoo_executor import OdooExecutor
from .errors import (
    OdooAccessError,
    OdooAuthenticationError,
    OdooError,
    OdooMissingRecordError,
    OdooServerError,
    OdooTransportError,
    OdooValidationError,
)
from .odoo_model import OdooModel
from .odoo_query import OdooQuery
from .odoo_rpc_executor import OdooRpcExecutor
from .x2many_commands import X2ManyCommand

# Phase A internal primitives are deliberately not re-exported here.
__all__ = [
    "OdooClient",
    "OdooConnectionSettings",
    "OdooError",
    "OdooAuthenticationError",
    "OdooAccessError",
    "OdooValidationError",
    "OdooMissingRecordError",
    "OdooTransportError",
    "OdooServerError",
    "OdooExecutor",
    "OdooModel",
    "OdooQuery",
    "OdooRpcExecutor",
    "X2ManyCommand",
]
