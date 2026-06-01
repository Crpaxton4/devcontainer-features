"""Public Odoo service exports.

The supported high-level API is now recordset-first. `OdooEnv`,
`DomainExpression`, and `OdooRecordset` are public exports alongside the
legacy compatibility wrappers that remain available during migration.
"""

from .odoo_client import OdooClient
from .odoo_config import OdooConnectionSettings
from .domain_expression import DomainExpression
from .odoo_executor import OdooExecutor
from .odoo_env import OdooEnv
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
from .odoo_recordset import OdooRecordset
from .odoo_rpc_executor import OdooRpcExecutor
from .x2many_commands import X2ManyCommand

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
    "OdooEnv",
    "OdooExecutor",
    "OdooModel",
    "OdooQuery",
    "OdooRecordset",
    "OdooRpcExecutor",
    "DomainExpression",
    "X2ManyCommand",
]
