from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

@dataclass(frozen=True)
class RelationValue:
    """Represent one adapted many2one relation returned by the SDK.

    This value object is necessary because Phase B turns raw many2one wire payloads
    into a stable Python-facing shape that preserves relation identity and any display
    label Odoo included.

    :param model_name: Name of the related Odoo model.
    :type model_name: str
    :param id: Identifier of the related record.
    :type id: int
    :param label: Optional display label returned by Odoo, defaults to None.
    :type label: Optional[str]
    """

    model_name: str
    id: int
    label: Optional[str] = None


@dataclass(frozen=True)
class RelationCollection:
    """Represent an adapted ordered collection of x2many related ids.

    This value object is necessary because Phase B needs one predictable Python shape
    for one2many and many2many read results instead of exposing only raw id lists.

    :param model_name: Name of the related Odoo model.
    :type model_name: str
    :param ids: Ordered related record ids, defaults to an empty tuple.
    :type ids: tuple[int, ...]
    """

    model_name: str
    ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        """Coerce stored relation ids into an immutable tuple.

        This hook is necessary because callers may pass any iterable, but the adapted
        value object should retain a stable immutable representation.

        :return: None.
        :rtype: None
        """
        object.__setattr__(self, "ids", tuple(self.ids))

    @classmethod
    def from_ids(
        cls,
        model_name: str,
        ids: Iterable[int],
    ) -> "RelationCollection":
        """Build a relation collection from any iterable of ids.

        This constructor helper is necessary because adapter code often receives list-
        like values from Odoo and needs one explicit way to normalize them into the
        immutable relation collection type.

        :param model_name: Name of the related Odoo model.
        :type model_name: str
        :param ids: Iterable of related record ids.
        :type ids: Iterable[int]
        :return: Immutable relation collection containing the provided ids.
        :rtype: RelationCollection
        """
        return cls(model_name=model_name, ids=tuple(ids))
