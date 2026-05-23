from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class RelationValue:
    """Immutable adapted many2one relation value."""

    model_name: str
    id: int
    label: Optional[str] = None


@dataclass(frozen=True)
class RelationCollection:
    """Immutable adapted x2many relation collection."""

    model_name: str
    ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "ids", tuple(self.ids))

    @classmethod
    def from_ids(
        cls,
        model_name: str,
        ids: Iterable[int],
    ) -> "RelationCollection":
        return cls(model_name=model_name, ids=tuple(ids))