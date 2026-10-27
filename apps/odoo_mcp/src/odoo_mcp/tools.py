"""The MCP tool surface, generated from the command registry.

Each command spec on the ``mcp`` surface becomes one tool function. Its
signature is the command's ``Args`` dataclass: one keyword-only parameter per
field, with the field's resolved type and default. FastMCP builds the JSON
schema from that signature, so the wire schema always matches the command.
"""

import dataclasses
import inspect
import typing
from typing import Any, Callable

from odoo_dev_core import CommandService
from odoo_dev_core.registry import CommandSpec


def _default(field: dataclasses.Field) -> Any:
    """Return the default of ``field``, or ``inspect.Parameter.empty`` if it has none."""
    if field.default is not dataclasses.MISSING:
        return field.default
    if field.default_factory is not dataclasses.MISSING:
        return field.default_factory()
    return inspect.Parameter.empty


def _tool(service: CommandService, spec: CommandSpec) -> Callable[..., Any]:
    """Build the tool function that runs command ``spec`` through ``service``."""
    hints = typing.get_type_hints(spec.args)
    parameters = [
        inspect.Parameter(
            field.name,
            inspect.Parameter.KEYWORD_ONLY,
            default=_default(field),
            annotation=hints[field.name],
        )
        for field in dataclasses.fields(spec.args)
    ]

    def tool(**kwargs: Any) -> Any:
        return service.execute(spec.id, **kwargs)

    tool.__name__ = tool.__qualname__ = spec.id
    tool.__doc__ = spec.description
    tool.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
    tool.__annotations__ = {**{p.name: p.annotation for p in parameters}, "return": Any}
    return tool


def build_tools(
    service: CommandService,
) -> dict[str, tuple[Callable[..., Any], str]]:
    """Return ``{tool name: (function, description)}`` for every ``mcp`` command."""
    return {
        spec.id: (_tool(service, spec), spec.description)
        for spec in service.registry.specs("mcp")
    }
