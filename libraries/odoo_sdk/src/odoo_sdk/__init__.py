"""Top-level package for odoo_sdk.

This module makes the `src` directory act as the `odoo_sdk` package
as configured in `pyproject.toml` (package-dir mapping).

It re-exports common symbols for a flatter, convenient public API.
The supported public surface is recordset-first, with
`DomainExpression`, `OdooRecordset`, and `OdooClient` as core types.
"""

from . import commands
from .client import OdooClient
from .commands import Registry
from .fields.commands import Command
from .state import OdooConnectionSettings
from .query.domain import Domain, DomainExpression
from .records import OdooRecordset
from .records.recordset import Record
from .transport import OdooExecutor, OdooJson2Executor, OdooRpcExecutor

__all__ = [
    "commands",
    "OdooClient",
    "OdooConnectionSettings",
    "OdooExecutor",
    "OdooRecordset",
    "OdooRpcExecutor",
    "Registry",
    "Domain",
    "DomainExpression",
    "Record",
]
