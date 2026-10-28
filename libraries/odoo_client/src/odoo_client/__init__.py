"""Top-level package for odoo_client.

This module makes the `src` directory act as the `odoo_client` package
as configured in `pyproject.toml` (package-dir mapping).

It re-exports common symbols for a flatter, convenient public API.
The supported public surface is recordset-first, with
`DomainExpression`, `OdooRecordset`, and `OdooClient` as core types.
The command layer lives in `odoo_dev_core`; the CLI, MCP server and TUI are
separate apps.
"""

from .client import OdooClient
from .settings import OdooConnectionSettings
from .query.domain import Domain, DomainExpression
from .records import OdooRecordset
from .records.recordset import Record
from .transport import (
    DeletionNotSupportedError,
    OdooAccessError,
    OdooAuthenticationError,
    OdooError,
    OdooExecutor,
    OdooJson2Executor,
    OdooMissingRecordError,
    OdooRpcExecutor,
    OdooServerError,
    OdooTransportError,
    OdooValidationError,
)

__all__ = [
    "OdooClient",
    "OdooConnectionSettings",
    "OdooExecutor",
    "OdooJson2Executor",
    "OdooRecordset",
    "OdooRpcExecutor",
    "Domain",
    "DomainExpression",
    "Record",
    "OdooError",
    "OdooAuthenticationError",
    "OdooAccessError",
    "OdooValidationError",
    "OdooMissingRecordError",
    "OdooTransportError",
    "OdooServerError",
    "DeletionNotSupportedError",
]
