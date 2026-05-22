from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Final, TypeAlias, Union

from ..utils.types import DomainCondition, DomainInput

_BOOLEAN_OPERATORS: Final[set[str]] = {"&", "|", "!"}


@dataclass(frozen=True, slots=True)
class _Condition:
    field: str
    operator: str
    value: Any

    def serialize(self) -> DomainCondition:
        return (self.field, self.operator, deepcopy(self.value))


@dataclass(frozen=True, slots=True)
class _BooleanExpression:
    operator: str
    operands: tuple[Union[_Condition, "_BooleanExpression"], ...]

    def __post_init__(self) -> None:
        if self.operator not in _BOOLEAN_OPERATORS:
            raise ValueError(f"Unsupported boolean operator: {self.operator!r}")
        if self.operator == "!" and len(self.operands) != 1:
            raise ValueError("Boolean operator '!' requires exactly one operand")
        if self.operator in {"&", "|"} and len(self.operands) < 2:
            raise ValueError(
                f"Boolean operator {self.operator!r} requires at least two operands"
            )


DomainNode: TypeAlias = Union[_Condition, _BooleanExpression]


@dataclass(frozen=True, slots=True)
class DomainExpression:
    """Canonical owner for domain normalization and XML-RPC serialization."""

    _nodes: tuple[DomainNode, ...] = ()

    @classmethod
    def normalize(cls, domain: DomainInput = None) -> DomainExpression:
        if isinstance(domain, cls):
            return domain
        if domain is None:
            return cls()
        return cls(_normalize_domain_nodes(domain, allow_empty=True))

    def serialize(self) -> list[Any]:
        serialized: list[Any] = []
        for node in self._nodes:
            item = _serialize_expression(node)
            if isinstance(item, list):
                serialized.extend(item)
            else:
                serialized.append(item)
        return serialized

    def is_empty(self) -> bool:
        return not self._nodes


def _normalize_domain_nodes(
    domain: Union[DomainInput, Sequence[Any]], *, allow_empty: bool
) -> tuple[DomainNode, ...]:
    if _is_condition(domain):
        return (_build_condition(domain),)
    if not _is_sequence(domain):
        raise ValueError("Domain input must be a condition or sequence of tokens")

    items = list(domain)
    if not items:
        if allow_empty:
            return ()
        raise ValueError("Nested domain groups cannot be empty")

    nodes: list[DomainNode] = []
    index = 0
    while index < len(items):
        node, index = _parse_expression(items, index)
        nodes.append(node)
    return tuple(nodes)


def _parse_expression(items: list[Any], index: int) -> tuple[DomainNode, int]:
    if index >= len(items):
        raise ValueError("Domain expression ended before all operands were provided")

    item = items[index]
    if isinstance(item, str):
        if item not in _BOOLEAN_OPERATORS:
            raise ValueError(f"Unsupported domain token: {item!r}")
        if item == "!":
            operand, next_index = _parse_expression(items, index + 1)
            return _BooleanExpression("!", (operand,)), next_index

        left, next_index = _parse_expression(items, index + 1)
        right, next_index = _parse_expression(items, next_index)
        return _BooleanExpression(item, (left, right)), next_index

    return _normalize_item(item), index + 1


def _normalize_item(item: Any) -> DomainNode:
    if _is_condition(item):
        return _build_condition(item)
    if not _is_sequence(item):
        raise ValueError(f"Unsupported domain item: {item!r}")

    nodes = _normalize_domain_nodes(item, allow_empty=False)
    if len(nodes) == 1:
        return nodes[0]
    return _BooleanExpression("&", nodes)


def _build_condition(condition: Sequence[Any]) -> _Condition:
    field, operator, value = condition
    return _Condition(field, operator, deepcopy(value))


def _is_condition(value: Any) -> bool:
    if not _is_sequence(value) or len(value) != 3:
        return False
    field, operator, _ = value
    return (
        isinstance(field, str)
        and field not in _BOOLEAN_OPERATORS
        and isinstance(operator, str)
    )


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _serialize_expression(node: DomainNode) -> Union[DomainCondition, list[Any]]:
    if not isinstance(node, _Condition):
        return node.serialize()
    if len(node.operands) == 1:
        return ["!", *_serialize_tokens(node.operands[0])]
    return _serialize_boolean(node.operator, node.operands)


def _serialize_boolean(operator: str, operands: tuple[DomainNode, ...]) -> list[Any]:
    if len(operands) == 2:
        return [
            operator,
            *_serialize_tokens(operands[0]),
            *_serialize_tokens(operands[1]),
        ]
    return [
        operator,
        *_serialize_tokens(_BooleanExpression(operator, operands[:-1])),
        *_serialize_tokens(operands[-1]),
    ]


def _serialize_tokens(node: DomainNode) -> list[Any]:
    item = _serialize_expression(node)
    if isinstance(item, list):
        return item
    return [item]