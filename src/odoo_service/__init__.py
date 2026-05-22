"""Public Odoo service exports.

Phase A guardrails intentionally preserve the current service exports.
New Phase A abstractions may exist internally before their public export
status is explicitly decided by the documentation-alignment task.
"""

from .odoo_client import OdooClient
from .odoo_config import OdooConnectionSettings
from .odoo_executor import OdooExecutor
from .odoo_model import OdooModel
from .odoo_query import OdooQuery
from .odoo_rpc_executor import OdooRpcExecutor

__all__ = [
    "OdooClient",
    "OdooConnectionSettings",
    "OdooExecutor",
    "OdooModel",
    "OdooQuery",
    "OdooRpcExecutor",
]
