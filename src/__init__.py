"""Top-level package for odoo_sdk.

This module makes the `src` directory act as the `odoo_sdk` package
as configured in `pyproject.toml` (package-dir mapping).

It re-exports common symbols for a flatter, convenient public API.
Phase A keeps that supported public surface centered on `OdooClient`
and the preserved compatibility wrappers. `OdooEnv`,
`DomainExpression`, and `OdooRecordset` remain internal Phase A
primitives and are intentionally excluded from `__all__`.
"""

from . import command_registry, odoo_service, utils

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("odoo_sdk")
except PackageNotFoundError:
    __version__ = "0.0.0"

from .odoo_service import (
    OdooClient,
    OdooConnectionSettings,
    OdooExecutor,
    OdooModel,
    OdooQuery,
    OdooRpcExecutor,
)
from .command_registry import CommandDispatcher
from .utils import Domain, Record

# Phase A keeps OdooEnv, DomainExpression, and OdooRecordset internal-only.
__all__ = [
    "command_registry",
    "odoo_service",
    "utils",
    "OdooClient",
    "OdooConnectionSettings",
    "OdooExecutor",
    "OdooModel",
    "OdooQuery",
    "OdooRpcExecutor",
    "CommandDispatcher",
    "Domain",
    "Record",
    "__version__",
]
