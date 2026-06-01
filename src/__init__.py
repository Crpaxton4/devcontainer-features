"""Top-level package for odoo_sdk.

This module makes the `src` directory act as the `odoo_sdk` package
as configured in `pyproject.toml` (package-dir mapping).

It re-exports common symbols for a flatter, convenient public API.
The supported public surface is recordset-first, with `OdooEnv`,
`DomainExpression`, and `OdooRecordset` exposed alongside legacy
compatibility wrappers.
"""

from . import command_registry, odoo_service

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("odoo_sdk")
except PackageNotFoundError:
    __version__ = "0.0.0"

from .odoo_service import (
    OdooClient,
    OdooConnectionSettings,
    OdooEnv,
    OdooExecutor,
    OdooModel,
    OdooQuery,
    OdooRecordset,
    OdooRpcExecutor,
)
from .odoo_service.domain_expression import Domain, DomainExpression
from .odoo_service.odoo_recordset import Record
from .command_registry import CommandDispatcher

__all__ = [
    "command_registry",
    "odoo_service",
    "OdooClient",
    "OdooConnectionSettings",
    "OdooEnv",
    "OdooExecutor",
    "OdooModel",
    "OdooQuery",
    "OdooRecordset",
    "OdooRpcExecutor",
    "CommandDispatcher",
    "Domain",
    "DomainExpression",
    "Record",
    "__version__",
]
