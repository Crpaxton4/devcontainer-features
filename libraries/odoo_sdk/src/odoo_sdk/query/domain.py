from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Final, TypeAlias, Union

from odoo_sdk._utils import _is_sequence

DomainCondition: TypeAlias = tuple[str, str, Any]
Domain: TypeAlias = list[DomainCondition]
DomainInput: TypeAlias = Union[DomainCondition, Sequence[Any]]

_BOOLEAN_OPERATORS: Final[set[str]] = {"&", "|", "!"}


@dataclass(frozen=True, slots=True)
class Condition:
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
class BooleanExpression:
    """Represent one normalized boolean operator in a domain expression tree.

    This internal node is necessary because Odoo's prefix boolean operators have
    strict arity rules that must be validated before the expression is serialized.

    :param operator: Boolean operator token such as ``&``, ``|``, or ``!``.
    :type operator: str
    :param operands: Operand nodes controlled by the operator.
    :type operands: tuple[Union[Condition, BooleanExpression], ...]
    """

    operator: str
    operands: tuple[Union[Condition, "BooleanExpression"], ...]

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


DomainNode: TypeAlias = Union[Condition, BooleanExpression]


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
    def normalize(cls, domain: DomainInput) -> DomainExpression:
        """Normalize supported domain input into a canonical expression object.

        This factory is necessary because callers may already hold a normalized
        expression or may pass one of several raw domain shapes that need validation
        and conversion before reuse.

        :param domain: Raw or normalized domain input, defaults to None.
        :type domain: DomainInput
        :return: Canonical domain expression for the provided input.
        :rtype: DomainExpression
        """
        if domain is None:
            return cls()
        if isinstance(domain, cls):
            return domain
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

    def field_names(self) -> set[str]:
        """Return every field name referenced by conditions in this expression.

        This method is necessary because in-memory evaluation needs to pre-fetch all
        referenced fields before iterating records.

        :return: Set of field name strings found in the expression tree.
        :rtype: set[str]
        """
        return _collect_domain_fields(self._nodes)

    def matches(self, record_values: dict[str, Any]) -> bool:
        """Evaluate the domain against an in-memory record value mapping.

        An empty domain matches every record.  Values in ``record_values`` may be
        adapted Python types (e.g. ``datetime.date``) or duck-typed relational objects
        that expose an ``ids`` attribute (e.g. ``OdooRecordset``).

        :param record_values: Adapted field values keyed by field name.
        :type record_values: dict[str, Any]
        :return: True when every domain node is satisfied by the provided values.
        :rtype: bool
        """
        if not self._nodes:
            return True
        return all(_evaluate_node(record_values, node) for node in self._nodes)

    @classmethod
    def AND(cls, iterable: Iterable[Any]) -> DomainExpression:
        """Combine an iterable of domains with logical AND.

        :param iterable: Iterable of ``DomainExpression`` instances or raw domain lists.
        :type iterable: Iterable[Any]
        :return: Combined domain expression.
        :rtype: DomainExpression
        """
        items = [cls.normalize(x) for x in iterable]
        if len(items) == 0:
            return cls.TRUE
        if len(items) == 1:
            return items[0]
        nodes = [n for item in items if (n := _to_domain_node(item)) is not None]
        if not nodes:
            return cls.TRUE
        if len(nodes) == 1:
            return cls((nodes[0],))
        return cls((BooleanExpression("&", tuple(nodes)),))

    @classmethod
    def OR(cls, iterable: Iterable[Any]) -> DomainExpression:
        """Combine an iterable of domains with logical OR.

        :param iterable: Iterable of ``DomainExpression`` instances or raw domain lists.
        :type iterable: Iterable[Any]
        :return: Combined domain expression.
        :rtype: DomainExpression
        """
        items = [cls.normalize(x) for x in iterable]
        if len(items) == 0:
            return cls.FALSE
        if len(items) == 1:
            return items[0]
        if any(item.is_empty() for item in items):
            return cls.TRUE
        nodes = tuple(_to_domain_node(item) for item in items)
        return cls((BooleanExpression("|", nodes),))  # type: ignore[arg-type]

    def __invert__(self) -> DomainExpression:
        """Return the logical negation of this domain.

        :return: New domain expression with a ``!`` node wrapping this expression.
        :rtype: DomainExpression
        """
        if self.is_empty():
            return type(self).FALSE
        node = _to_domain_node(self)
        assert node is not None
        return type(self)((BooleanExpression("!", (node,)),))

    def __and__(self, other: Any) -> DomainExpression:
        """Return the logical AND of this domain and another.

        :param other: Another ``DomainExpression`` or raw domain list.
        :type other: Any
        :return: Combined domain expression.
        :rtype: DomainExpression
        """
        return type(self).AND([self, type(self).normalize(other)])

    def __or__(self, other: Any) -> DomainExpression:
        """Return the logical OR of this domain and another.

        :param other: Another ``DomainExpression`` or raw domain list.
        :type other: Any
        :return: Combined domain expression.
        :rtype: DomainExpression
        """
        return type(self).OR([self, type(self).normalize(other)])


def _to_domain_node(expr: DomainExpression) -> DomainNode | None:
    """Reduce a DomainExpression to a single DomainNode for use in boolean trees.

    This helper is necessary because composition methods need to embed multi-node
    expressions as a single operand inside a parent BooleanExpression.

    :param expr: Domain expression to reduce.
    :type expr: DomainExpression
    :return: None for empty (TRUE) expressions; single node or implicit AND wrapper.
    :rtype: DomainNode | None
    """
    if not expr._nodes:
        return None
    if len(expr._nodes) == 1:
        return expr._nodes[0]
    return BooleanExpression("&", expr._nodes)


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
            return _parse_binary_expression(item, items, index)
        return _parse_unary_expression(items, index)

    return _normalize_item(item), index + 1


def _parse_binary_expression(
    operator: str, items: list[Any], index: int
) -> tuple[DomainNode, int]:
    """Parse a binary boolean expression (``&`` or ``|``) from a token sequence.

    :param operator: The binary operator token (``&`` or ``|``).
    :type operator: str
    :param items: Flat list of domain tokens.
    :type items: list[Any]
    :param index: Index of the operator token.
    :type index: int
    :return: Binary expression node and the next unread token index.
    :rtype: tuple[DomainNode, int]
    """
    left, next_index = _parse_expression(items, index + 1)
    right, next_index = _parse_expression(items, next_index)
    return BooleanExpression(operator, (left, right)), next_index


def _parse_unary_expression(items: list[Any], index: int) -> tuple[DomainNode, int]:
    """Parse a unary boolean expression (``!``) from a token sequence.

    :param items: Flat list of domain tokens.
    :type items: list[Any]
    :param index: Index of the ``!`` operator token.
    :type index: int
    :return: Negation expression node and the next unread token index.
    :rtype: tuple[DomainNode, int]
    """
    operand, next_index = _parse_expression(items, index + 1)
    return BooleanExpression("!", (operand,)), next_index


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
    return BooleanExpression("&", nodes)


def _build_condition(condition: Sequence[Any]) -> Condition:
    """Create a normalized condition node from a raw condition triple.

    This helper is necessary so normalization can deep-copy mutable operand values and
    guarantee a uniform internal condition type.

    :param condition: Raw field, operator, and value triple.
    :type condition: Sequence[Any]
    :return: Normalized condition node.
    :rtype: _Condition
    """
    field, operator, value = condition
    return Condition(field, operator, deepcopy(value))


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


def _serialize_expression(node: DomainNode) -> Union[DomainCondition, list[Any]]:
    """Serialize one normalized node into Odoo domain tokens.

    This helper is necessary because condition nodes and boolean nodes serialize to
    different shapes, but higher-level callers need one common entry point.

    :param node: Normalized domain node to serialize.
    :type node: DomainNode
    :return: Serialized condition tuple or boolean token list.
    :rtype: Union[DomainCondition, list[Any]]
    """
    if isinstance(node, Condition):
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
        raise ValueError(
            f"Boolean operator {operator!r} requires at least two operands"
        )
    if operand_count > 2:
        return [
            operator,
            *_serialize_tokens(BooleanExpression(operator, operands[:-1])),
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


# ---------------------------------------------------------------------------
# In-memory domain evaluation helpers
# ---------------------------------------------------------------------------


def _extract_comparison_value(value: Any) -> Any:
    """Normalize an adapted field value to a primitive suitable for comparison.

    Duck-types relational objects that expose an ``ids`` attribute (e.g.
    ``OdooRecordset``) to avoid importing that class here.

    :param value: Adapted field value to normalize.
    :type value: Any
    :return: Comparable primitive (int, tuple, False, or the value unchanged).
    :rtype: Any
    """
    if hasattr(value, "ids"):
        ids = tuple(value.ids)
        if not ids:
            return False
        if len(ids) == 1:
            return ids[0]
        return ids
    return value


def _sql_like_to_regex(pattern: str, *, case_sensitive: bool) -> re.Pattern[str]:
    """Convert an SQL LIKE pattern to a compiled regular expression.

    ``%`` matches any sequence of characters; ``_`` matches any single character;
    all other regex metacharacters are escaped.

    :param pattern: SQL LIKE pattern to convert.
    :type pattern: str
    :param case_sensitive: When False, compile with ``re.IGNORECASE``.
    :type case_sensitive: bool
    :return: Compiled regular expression equivalent to the LIKE pattern.
    :rtype: re.Pattern[str]
    """
    parts: list[str] = []
    for char in pattern:
        if char == "%":
            parts.append(".*")
        elif char == "_":
            parts.append(".")
        else:
            parts.append(re.escape(char))
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile("".join(parts), flags)


def _match_equality(cmp: Any, domain_value: Any, *, negate: bool) -> bool:
    """Evaluate an ``=`` or ``!=`` condition against a normalized field value.

    :param cmp: Normalized comparison value (from ``_extract_comparison_value``).
    :type cmp: Any
    :param domain_value: Operand from the domain condition.
    :type domain_value: Any
    :param negate: When True, evaluate ``!=``; otherwise evaluate ``=``.
    :type negate: bool
    :return: True when the condition is satisfied.
    :rtype: bool
    """
    if domain_value is False or domain_value is None:
        result = not cmp
    else:
        result = cmp == domain_value
    return (not result) if negate else result


def _match_membership(cmp: Any, domain_value: Any, *, negate: bool) -> bool:
    """Evaluate an ``in`` or ``not in`` condition against a normalized field value.

    :param cmp: Normalized comparison value.
    :type cmp: Any
    :param domain_value: Collection operand from the domain condition.
    :type domain_value: Any
    :param negate: When True, evaluate ``not in``; otherwise evaluate ``in``.
    :type negate: bool
    :return: True when the condition is satisfied.
    :rtype: bool
    """
    if isinstance(cmp, tuple):
        result = any(v in domain_value for v in cmp)
        return (not result) if negate else result
    return (cmp not in domain_value) if negate else (cmp in domain_value)


def _match_like_pattern(
    cmp: Any, domain_value: Any, *, case_sensitive: bool, negate: bool
) -> bool:
    """Evaluate a LIKE/ILIKE condition against a normalized field value.

    :param cmp: Normalized field value (expected to be a string).
    :type cmp: Any
    :param domain_value: SQL LIKE pattern from the domain condition.
    :type domain_value: Any
    :param case_sensitive: When True, use case-sensitive matching.
    :type case_sensitive: bool
    :param negate: When True, negate the match result (NOT LIKE / NOT ILIKE).
    :type negate: bool
    :return: True when the condition is satisfied.
    :rtype: bool
    """
    if not isinstance(cmp, str) or not isinstance(domain_value, str):
        return negate
    matched = bool(
        _sql_like_to_regex(domain_value, case_sensitive=case_sensitive).fullmatch(cmp)
    )
    return (not matched) if negate else matched


_COMPARISON_OP_DISPATCH: dict[str, Any] = {
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
}


def _match_condition(field_value: Any, operator: str, domain_value: Any) -> bool:
    """Evaluate one domain condition operator against an adapted field value.

    :param field_value: Adapted value from the record field cache.
    :type field_value: Any
    :param operator: Odoo domain operator string.
    :type operator: str
    :param domain_value: Operand value from the domain condition.
    :type domain_value: Any
    :raises NotImplementedError: For ``child_of`` and ``parent_of`` which require
        server-side hierarchy information.
    :raises ValueError: For unrecognised operators.
    :return: True when the condition is satisfied.
    :rtype: bool
    """
    cmp = _extract_comparison_value(field_value)

    if operator == "=":
        return _match_equality(cmp, domain_value, negate=False)
    if operator == "!=":
        return _match_equality(cmp, domain_value, negate=True)
    if operator in _COMPARISON_OP_DISPATCH:
        return _COMPARISON_OP_DISPATCH[operator](cmp, domain_value)
    if operator == "in":
        return _match_membership(cmp, domain_value, negate=False)
    if operator == "not in":
        return _match_membership(cmp, domain_value, negate=True)
    if operator in ("like", "=like"):
        return _match_like_pattern(cmp, domain_value, case_sensitive=True, negate=False)
    if operator in ("ilike", "=ilike"):
        return _match_like_pattern(
            cmp, domain_value, case_sensitive=False, negate=False
        )
    if operator == "not like":
        return _match_like_pattern(cmp, domain_value, case_sensitive=True, negate=True)
    if operator == "not ilike":
        return _match_like_pattern(cmp, domain_value, case_sensitive=False, negate=True)
    if operator in ("child_of", "parent_of"):
        raise NotImplementedError(
            f"Operator {operator!r} requires server-side hierarchy information "
            "and is not supported in filtered_domain."
        )
    raise ValueError(
        f"Unsupported domain operator for in-memory evaluation: {operator!r}"
    )


def _evaluate_node(record_values: dict[str, Any], node: DomainNode) -> bool:
    """Recursively evaluate a normalized domain node against record field values.

    :param record_values: Adapted field values keyed by field name.
    :type record_values: dict[str, Any]
    :param node: Normalized domain node (condition or boolean expression).
    :type node: DomainNode
    :return: True when the node is satisfied.
    :rtype: bool
    """
    if isinstance(node, Condition):
        field_value = record_values.get(node.field)
        return _match_condition(field_value, node.operator, node.value)
    # BooleanExpression
    if node.operator == "!":
        return not _evaluate_node(record_values, node.operands[0])
    if node.operator == "&":
        return all(_evaluate_node(record_values, op) for op in node.operands)
    # "|"
    return any(_evaluate_node(record_values, op) for op in node.operands)


def _collect_domain_fields(nodes: tuple[DomainNode, ...]) -> set[str]:
    """Collect every field name referenced in a domain node tree.

    :param nodes: Tuple of normalized domain nodes to inspect.
    :type nodes: tuple[DomainNode, ...]
    :return: Set of unique field names found in all leaf conditions.
    :rtype: set[str]
    """
    result: set[str] = set()
    for node in nodes:
        if isinstance(node, Condition):
            result.add(node.field)
        else:  # BooleanExpression
            result |= _collect_domain_fields(node.operands)
    return result


# Class-level constants for domain composition.
DomainExpression.TRUE = DomainExpression()
DomainExpression.FALSE = DomainExpression((Condition("id", "=", False),))

# Private aliases used by internal tests to access implementation-level nodes
# without importing the public names, signalling these are internal details.
_Condition = Condition
_BooleanExpression = BooleanExpression
