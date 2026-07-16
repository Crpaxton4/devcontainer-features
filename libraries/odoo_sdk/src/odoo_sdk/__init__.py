"""Top-level package for odoo_sdk.

This module makes the `src` directory act as the `odoo_sdk` package
as configured in `pyproject.toml` (package-dir mapping).

It re-exports common symbols for a flatter, convenient public API.
The supported public surface is recordset-first, with
`DomainExpression`, `OdooRecordset`, and `OdooClient` as core types.
`Command`/`Registry` back the command layer, and `OdooMCPServer` exposes
those commands as MCP tools.

`OdooMCPServer` is resolved lazily via a module-level ``__getattr__``
(PEP 562): importing it pulls in ``fastmcp``, which adds several seconds of
import cost, so it is kept off the eager import path used by the
recordset-first majority while still being advertised on the public surface.
"""

from typing import TYPE_CHECKING, Any

from . import commands
from .client import OdooClient
from .commands import Command, Registry
from .state import OdooConnectionSettings
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

if TYPE_CHECKING:  # pragma: no cover
    from .mcp.server import OdooMCPServer

__all__ = [
    "commands",
    "Command",
    "OdooClient",
    "OdooConnectionSettings",
    "OdooExecutor",
    "OdooJson2Executor",
    "OdooMCPServer",
    "OdooRecordset",
    "OdooRpcExecutor",
    "Registry",
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


def __getattr__(name: str) -> Any:
    """Resolve lazily exported public symbols (PEP 562).

    ``OdooMCPServer`` lives in :mod:`odoo_sdk.mcp.server`, whose import pulls in
    ``fastmcp`` at a cost of several seconds. Routing it through ``__getattr__``
    keeps ``import odoo_sdk`` cheap for consumers that never touch MCP while
    still exposing the symbol at the package top level for those that do.

    :param name: Attribute requested on the package.
    :type name: str
    :return: The resolved public symbol.
    :rtype: Any
    :raises AttributeError: If ``name`` is not a lazily exported symbol.
    """

    if name == "OdooMCPServer":
        from .mcp.server import OdooMCPServer

        return OdooMCPServer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
