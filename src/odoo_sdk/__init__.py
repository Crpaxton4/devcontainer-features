"""Top-level package for odoo_sdk.

This module makes the `src` directory act as the `odoo_sdk` package
as configured in `pyproject.toml` (package-dir mapping).

It re-exports common symbols for a flatter, convenient public API.
The supported public surface is recordset-first, with `OdooEnv`,
`DomainExpression`, and `OdooRecordset` exposed alongside legacy
compatibility wrappers.
"""

from importlib.metadata import PackageNotFoundError, version

from . import command_registry

try:
    __version__ = version("odoo_sdk")
except PackageNotFoundError:
    __version__ = "0.0.0"

from .client import OdooClient
from .config import OdooConnectionSettings
from .env import OdooEnv
from .fields.commands import Command
from .query.domain import Domain, DomainExpression
from .records import OdooRecordset
from .records.recordset import Record
from .transport import OdooExecutor, OdooRpcExecutor
from .command_registry import CommandDispatcher

__all__ = [
    "command_registry",
    "OdooClient",
    "OdooConnectionSettings",
    "OdooEnv",
    "OdooExecutor",
    "OdooRecordset",
    "OdooRpcExecutor",
    "CommandDispatcher",
    "Domain",
    "DomainExpression",
    "Record",
    "__version__",
]
