"""Shims plus genuinely shared code — ``utilities/`` was dissolved by #717.

What used to live here spanned all three ADR-005 layers, so the package was
split by interaction direction:

* Odoo-facing service helpers (``odoo_helpers``, ``activities``,
  ``attachments``, ``knowledge``, ``mail_status``, ``logged_lines``) moved to
  the data package :mod:`odoo_sdk.services`.
* Local run/session tracking helpers (``env``, ``runs``, ``stats``,
  ``checkpoint``) moved to the core package :mod:`odoo_sdk.tracking`.
* ``prompt_messages`` moved to :mod:`odoo_sdk.mcp.prompts.messages` (the
  builders are surface content).
* ``format_chatter`` moved to the shared kernel (:mod:`odoo_sdk._utils`).
* :mod:`odoo_sdk.utilities.html` STAYS here as genuinely shared code — a
  pure text-conversion module importable from any layer.

Every old import path keeps working: the submodules are ``sys.modules``
aliasing shims and this ``__init__`` lazily forwards the names it used to
re-export (PEP 562), emitting a :class:`DeprecationWarning` for moved names.
Shims are kept for at least two minor releases and are excluded from the
ADR-005 import-linter contracts by design (they re-export across layers on
purpose).
"""

import importlib
import warnings
from typing import Any

from .html import html_to_markdown

__all__ = [
    "assert_sdk_configured",
    "html_to_markdown",
    "resolve_many2one",
    "format_chatter",
    "name_search_projects",
    "name_search_tasks",
    "get_employee_id",
    "post_chatter_note",
    "get_task_chatter",
    "get_task_detail",
]

# Old package-level name -> the module that canonically owns it now. The
# submodule names themselves ("env", "odoo_helpers", ...) are deliberately
# absent: the import system resolves those through the per-module shim files.
_MOVED = {
    "assert_sdk_configured": "odoo_sdk.tracking.env",
    "format_chatter": "odoo_sdk._utils",
    "resolve_many2one": "odoo_sdk.services.odoo_helpers",
    "name_search_projects": "odoo_sdk.services.odoo_helpers",
    "name_search_tasks": "odoo_sdk.services.odoo_helpers",
    "get_employee_id": "odoo_sdk.services.odoo_helpers",
    "post_chatter_note": "odoo_sdk.services.odoo_helpers",
    "get_task_chatter": "odoo_sdk.services.odoo_helpers",
    "get_task_detail": "odoo_sdk.services.odoo_helpers",
}


def __getattr__(name: str) -> Any:
    """Forward moved names to their new homes with a deprecation warning."""
    try:
        target = _MOVED[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    warnings.warn(
        f"odoo_sdk.utilities.{name} is deprecated; import it from {target} "
        "instead (shim kept for at least two minor releases; #717).",
        DeprecationWarning,
        stacklevel=2,
    )
    return getattr(importlib.import_module(target), name)
