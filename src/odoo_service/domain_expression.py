from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Final, TypeAlias, Union

DomainCondition: TypeAlias = tuple[str, str, Any]
Domain: TypeAlias = list[DomainCondition]
DomainInput: TypeAlias = Union[DomainCondition, Sequence[Any], None]

_BOOLEAN_OPERATORS: Final[set[str]] = {"&", "|", "!"}


@dataclass(frozen=True, slots=True)
class _Condition:
    """Represent one normalized leaf condition in an Odoo domain tree.

    This internal node is necessary because the serializer needs a stable typed shape
    for field/operator/value triples instead of repeatedly inspecting arbitrary raw
    sequences.

    :param field: Field name targeted by the condition.
    :type field: str
    :param operator: Odoo comparison operator for the condition.
    :type operator: str
    :param value: Operand value compared against the field.
    :type value: Any
    """

    field: str
    operator: str
    value: Any

    def serialize(self) -> DomainCondition:
        """Serialize the condition back into an Odoo domain triple.

        This helper is necessary because normalized condition nodes must round-trip to
        XML-RPC-safe tuple payloads without exposing internal node objects.

        :return: Field, operator, and deep-copied value tuple.
        :rtype: DomainCondition
        """
        return (self.field, self.operator, deepcopy(self.value))


@dataclass(frozen=True, slots=True)
class _BooleanExpression:
    """Represent one normalized boolean operator in a domain expression tree.

    This internal node is necessary because Odoo's prefix boolean operators have
    strict arity rules that must be validated before the expression is serialized.

    :param operator: Boolean operator token such as ``&``, ``|``, or ``!``.
    :type operator: str
    :param operands: Operand nodes controlled by the operator.
    :type operands: tuple[Union[_Condition, _BooleanExpression], ...]
    """

    operator: str
    operands: tuple[Union[_Condition, "_BooleanExpression"], ...]

    def __post_init__(self) -> None:
        """Validate operator support and operand arity after construction.

        This validation hook is necessary because invalid boolean trees should fail at
        normalization time instead of being serialized into malformed Odoo domains.

        :raises ValueError: Raised when the operator is unsupported or the operand
            count is invalid.
        :return: None.
        :rtype: None
        """
        if self.operator not in _BOOLEAN_OPERATORS:
            raise ValueError(f"Unsupported boolean operator: {self.operator!r}")
        if self.operator in {"&", "|"} and len(self.operands) < 2:
            raise ValueError(
                f"Boolean operator {self.operator!r} requires at least two operands"
            )
        if self.operator not in {"&", "|"} and len(self.operands) != 1:
            raise ValueError("Boolean operator '!' requires exactly one operand")


DomainNode: TypeAlias = Union[_Condition, _BooleanExpression]


@dataclass(frozen=True, slots=True)
class DomainExpression:
    """Own normalized domain trees and serialize them for XML-RPC execution.

    This value object is necessary because raw domain input can arrive as a single
    condition, a flat prefix token stream, or nested boolean groups, while the rest of
    the SDK needs one canonical representation that can be safely reused.

    :param _nodes: Normalized domain nodes stored in evaluation order.
    :type _nodes: tuple[DomainNode, ...]
    """

    _nodes: tuple[DomainNode, ...] = ()

    @classmethod
    def normalize(cls, domain: DomainInput = None) -> DomainExpression:
        """Normalize supported domain input into a canonical expression object.

        This factory is necessary because callers may already hold a normalized
        expression or may pass one of several raw domain shapes that need validation
        and conversion before reuse.

        :param domain: Raw or normalized domain input, defaults to None.
        :type domain: DomainInput
        :return: Canonical domain expression for the provided input.
        :rtype: DomainExpression
        """
        if isinstance(domain, cls):
            return domain
        if domain is None:
            return cls()
        return cls(_normalize_domain_nodes(domain, allow_empty=True))

    def serialize(self) -> list[Any]:
        """Serialize the normalized expression into Odoo prefix tokens.

        This method is necessary because executor and recordset flows still speak the
        raw XML-RPC domain format even though the SDK normalizes domains internally.

        :return: Serialized domain tokens ready for Odoo RPC calls.
        :rtype: list[Any]
        """
        serialized: list[Any] = []
        for node in self._nodes:
            item = _serialize_expression(node)
            if isinstance(item, list):
                serialized.extend(item)
            else:
                serialized.append(item)
        return serialized

    def is_empty(self) -> bool:
        """Report whether the expression contains any domain nodes.

        This check is necessary for callers that need to distinguish an explicit empty
        filter from a non-empty search expression.

        :return: True when the expression has no nodes.
        :rtype: bool
        """
        return not self._nodes


def _normalize_domain_nodes(
    domain: Union[DomainInput, Sequence[Any]], *, allow_empty: bool
) -> tuple[DomainNode, ...]:
    """Normalize raw domain input into a tuple of internal domain nodes.

    This helper is necessary because top-level domain input can mix single
    conditions, flat token streams, and nested groups, all of which must converge on
    the same internal node representation.

    :param domain: Raw domain input to normalize.
    :type domain: Union[DomainInput, Sequence[Any]]
    :param allow_empty: Whether an empty sequence is valid at this position.
    :type allow_empty: bool
    :raises ValueError: Raised when the input shape is not a supported domain form.
    :return: Normalized domain nodes.
    :rtype: tuple[DomainNode, ...]
    """
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
    """Parse one boolean or condition expression from a token sequence.

    This recursive parser is necessary because Odoo boolean domains use prefix tokens
    that must be consumed in order while preserving the remaining index.

    :param items: Flat list of domain tokens.
    :type items: list[Any]
    :param index: Current token index to parse from.
    :type index: int
    :raises ValueError: Raised when tokens end early or contain unsupported operators.
    :return: Parsed node and the next unread token index.
    :rtype: tuple[DomainNode, int]
    """
    if index >= len(items):
        raise ValueError("Domain expression ended before all operands were provided")

    item = items[index]
    if isinstance(item, str):
        if item not in _BOOLEAN_OPERATORS:
            raise ValueError(f"Unsupported domain token: {item!r}")
        if item in {"&", "|"}:
            left, next_index = _parse_expression(items, index + 1)
            right, next_index = _parse_expression(items, next_index)
            return _BooleanExpression(item, (left, right)), next_index

        operand, next_index = _parse_expression(items, index + 1)
        return _BooleanExpression("!", (operand,)), next_index

    return _normalize_item(item), index + 1


def _normalize_item(item: Any) -> DomainNode:
    """Normalize one nested domain item into a node.

    This helper is necessary because nested lists can represent either a single
    condition or an implicit ``&`` group and must be interpreted consistently.

    :param item: Nested domain item to normalize.
    :type item: Any
    :raises ValueError: Raised when the item is not a supported domain fragment.
    :return: Normalized domain node.
    :rtype: DomainNode
    """
    if _is_condition(item):
        return _build_condition(item)
    if not _is_sequence(item):
        raise ValueError(f"Unsupported domain item: {item!r}")

    nodes = _normalize_domain_nodes(item, allow_empty=False)
    if len(nodes) == 1:
        return nodes[0]
    return _BooleanExpression("&", nodes)


def _build_condition(condition: Sequence[Any]) -> _Condition:
    """Create a normalized condition node from a raw condition triple.

    This helper is necessary so normalization can deep-copy mutable operand values and
    guarantee a uniform internal condition type.

    :param condition: Raw field, operator, and value triple.
    :type condition: Sequence[Any]
    :return: Normalized condition node.
    :rtype: _Condition
    """
    field, operator, value = condition
    return _Condition(field, operator, deepcopy(value))


def _is_condition(value: Any) -> bool:
    """Return whether a value looks like a domain condition triple.

    This predicate is necessary because the normalizer must distinguish field
    conditions from boolean token sequences before recursing.

    :param value: Candidate value to inspect.
    :type value: Any
    :return: True when the value is a valid-looking condition triple.
    :rtype: bool
    """
    if not _is_sequence(value) or len(value) != 3:
        return False
    field, operator, _ = value
    return (
        isinstance(field, str)
        and field not in _BOOLEAN_OPERATORS
        and isinstance(operator, str)
    )


def _is_sequence(value: Any) -> bool:
    """Return whether a value is a non-string sequence.

    This helper is necessary because domain parsing accepts list-like containers but
    must reject strings and bytes, which are sequences in Python but not domain
    structures here.

    :param value: Candidate value to inspect.
    :type value: Any
    :return: True when the value is a supported sequence type.
    :rtype: bool
    """
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _serialize_expression(node: DomainNode) -> Union[DomainCondition, list[Any]]:
    """Serialize one normalized node into Odoo domain tokens.

    This helper is necessary because condition nodes and boolean nodes serialize to
    different shapes, but higher-level callers need one common entry point.

    :param node: Normalized domain node to serialize.
    :type node: DomainNode
    :return: Serialized condition tuple or boolean token list.
    :rtype: Union[DomainCondition, list[Any]]
    """
    if isinstance(node, _Condition):
        return node.serialize()
    if node.operator == "!":
        return ["!", *_serialize_tokens(node.operands[0])]
    return _serialize_boolean(node.operator, node.operands)


def _serialize_boolean(operator: str, operands: tuple[DomainNode, ...]) -> list[Any]:
    """Serialize a boolean node into Odoo's prefix token form.

    This helper is necessary because Odoo only accepts binary ``&`` and ``|`` tokens,
    so larger operand groups must be re-associated into nested prefix expressions.

    :param operator: Boolean operator to serialize.
    :type operator: str
    :param operands: Operand nodes controlled by the operator.
    :type operands: tuple[DomainNode, ...]
    :return: Prefix-ordered domain tokens.
    :rtype: list[Any]
    """
    operand_count = len(operands)
    if operand_count < 2:
        raise ValueError(f"Boolean operator {operator!r} requires at least two operands")
    if operand_count > 2:
        return [
            operator,
            *_serialize_tokens(_BooleanExpression(operator, operands[:-1])),
            *_serialize_tokens(operands[-1]),
        ]

    left, right = operands
    return [
        operator,
        *_serialize_tokens(left),
        *_serialize_tokens(right),
    ]


def _serialize_tokens(node: DomainNode) -> list[Any]:
    """Wrap a serialized node into a list of domain tokens.

    This helper is necessary because callers that compose boolean expressions need a
    uniform list form regardless of whether a node serialized to one tuple or many
    prefix tokens.

    :param node: Normalized node to serialize.
    :type node: DomainNode
    :return: Serialized tokens for the node.
    :rtype: list[Any]
    """
    item = _serialize_expression(node)
    if isinstance(item, list):
        return item
    return [item]
