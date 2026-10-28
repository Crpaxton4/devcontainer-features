"""String-keyed command registry and the service that executes a command by id.

A command is a :class:`CommandSpec`: an id, a ``handler(services, args)``
function, a frozen ``Args`` dataclass and a description. Builtin modules record
their spec with the :func:`command` decorator. :func:`default_registry` imports
the builtins and returns a registry that holds every recorded spec.
"""

from __future__ import annotations

import dataclasses
import importlib
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .ports import Services


class CommandArgumentError(ValueError):
    """The keyword arguments do not fit the command's ``Args`` dataclass.

    A ``ValueError`` subclass, so the dispatch error boundary treats it as
    invalid input. A surface can catch it first to tell a usage error from a
    failure inside the handler.
    """


@dataclass(frozen=True)
class CommandSpec:
    """One command: how to call it, what arguments it takes, where it shows."""

    id: str
    handler: Callable[[Services, Any], Any]
    args: type
    description: str
    gated: bool = False
    surfaces: frozenset[str] = frozenset({"cli", "mcp"})


class CommandRegistry:
    """Hold command specs by id."""

    def __init__(self) -> None:
        self._specs: dict[str, CommandSpec] = {}

    def register(self, spec: CommandSpec) -> None:
        """Add ``spec``. Raise ``ValueError`` when its id is taken."""
        if spec.id in self._specs:
            raise ValueError(f"Duplicate command id {spec.id!r}.")
        self._specs[spec.id] = spec

    def get(self, id: str) -> CommandSpec:
        """Return the spec for ``id``. Raise ``KeyError`` when there is none."""
        return self._specs[id]

    def ids(self) -> list[str]:
        """Return every registered id, sorted."""
        return sorted(self._specs)

    def specs(self, surface: Optional[str] = None) -> list[CommandSpec]:
        """Return the specs sorted by id, only those on ``surface`` when given."""
        return [
            self._specs[id]
            for id in self.ids()
            if surface is None or surface in self._specs[id].surfaces
        ]


#: Specs recorded by :func:`command` while the builtin modules import.
_BUILTIN_SPECS: dict[str, CommandSpec] = {}


def command(
    id: str,
    *,
    args: type,
    description: str,
    gated: bool = False,
    surfaces: frozenset[str] = frozenset({"cli", "mcp"}),
) -> Callable[[Callable[[Services, Any], Any]], Callable[[Services, Any], Any]]:
    """Record the decorated handler as a builtin command and return it unchanged."""

    def decorate(
        handler: Callable[[Services, Any], Any],
    ) -> Callable[[Services, Any], Any]:
        if id in _BUILTIN_SPECS:
            raise ValueError(f"Duplicate command id {id!r}.")
        _BUILTIN_SPECS[id] = CommandSpec(
            id=id,
            handler=handler,
            args=args,
            description=description,
            gated=gated,
            surfaces=surfaces,
        )
        return handler

    return decorate


def default_registry() -> CommandRegistry:
    """Return a registry that holds every builtin command."""
    # A dynamic import: the builtins sit above the registry in the layer map, so a
    # static import here would point upward.
    importlib.import_module("odoo_dev_core.builtins")
    registry = CommandRegistry()
    for spec in _BUILTIN_SPECS.values():
        registry.register(spec)
    return registry


class CommandService:
    """Execute commands by id against one :class:`Services` bundle."""

    def __init__(self, registry: CommandRegistry, services: Services) -> None:
        self._registry = registry
        self._services = services

    @property
    def registry(self) -> CommandRegistry:
        return self._registry

    @property
    def services(self) -> Services:
        return self._services

    def execute(self, id: str, /, **kwargs: Any) -> Any:
        """Run command ``id`` with ``kwargs`` as its arguments.

        :raises KeyError: When no command has this id.
        :raises CommandArgumentError: (a ``ValueError``) when ``kwargs`` has an
            unknown or a missing field.
        """
        spec = self._registry.get(id)
        try:
            args = spec.args(**kwargs)
        except TypeError as exc:
            fields = ", ".join(f.name for f in dataclasses.fields(spec.args))
            raise CommandArgumentError(
                f"{id}: {exc} (accepted fields: {fields})"
            ) from exc
        return spec.handler(self._services, args)
