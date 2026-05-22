"""Top-level package for odoo_sdk.

This module makes the `src` directory act as the `odoo_sdk` package
as configured in `pyproject.toml` (package-dir mapping).

It re-exports common symbols for a flatter, convenient public API.
Phase A guardrails intentionally preserve these exports until the
dedicated export-alignment task decides whether new abstractions become
supported public surfaces.
"""

from . import command_registry, odoo_service, utils

try:
    from importlib.metadata import version, PackageNotFoundError
except ImportError:
    from importlib_metadata import version, PackageNotFoundError  # type: ignore

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
