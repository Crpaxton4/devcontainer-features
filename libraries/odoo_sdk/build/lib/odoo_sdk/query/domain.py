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

_VALID_LEAF_OPERATORS: Final[frozenset[str]] = frozenset(
    {
        "=",
        "!=",
        ">",
        ">=",
        "<",
        "<=",
        "=?",
        "like",
        "not like",
        "ilike",
        "not ilike",
        "=like",
        "=ilike",
        "in",
        "not in",
        "child_of",
        "parent_of",
        "any",
        "not any",
    }
)


@dataclass(frozen=True, slots=True)
class Condition:
    """Represent one normalized leaf condition (field/operator/value) in a domain tree."""

    field: str
    operator: str
    value: Any

    def serialize(self) -> DomainCondition:
        """Serialize the condition into an Odoo domain triple with a deep-copied value."""
        return (self.field, self.operator, deepcopy(self.value))


@dataclass(frozen=True, slots=True)
class BooleanExpression:
    """Represent one normalized boolean operator (``&``, ``|``, ``!``) in a domain tree."""

    operator: str
    operands: tuple[Union[Condition, "BooleanExpression"], ...]

    def __post_init__(self) -> None:
        """Validate operator support and operand arity after construction.

        :raises ValueError: When the operator is unsupported or the operand count
            is invalid.
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

    Raw domain input may arrive as a single condition, a flat prefix token stream,
    or nested boolean groups; this is the one canonical representation the rest of
    the SDK reuses.
    """

    _nodes: tuple[DomainNode, ...] = ()

    @classmethod
    def normalize(cls, domain: DomainInput) -> DomainExpression:
        """Normalize supported domain input into a canonical expression object."""
        if domain is None:
            return cls()
        if isinstance(domain, cls):
            return domain
        return cls(_normalize_domain_nodes(domain, allow_empty=True))

    def serialize(self) -> list[Any]:
        """Serialize the normalized expression into Odoo prefix tokens for RPC."""
        serialized: list[Any] = []
        for node in self._nodes:
            item = _serialize_expression(node)
            if isinstance(item, list):
                serialized.extend(item)
            else:
                serialized.append(item)
        return serialized

    def is_empty(self) -> bool:
        """Report whether the expression contains no domain nodes."""
        return not self._nodes

    def field_names(self) -> set[str]:
        """Return every field name referenced by conditions in this expression."""
        return _collect_domain_fields(self._nodes)

    def matches(self, record_values: dict[str, Any]) -> bool:
        """Evaluate the domain against an in-memory record value mapping.

        An empty domain matches every record. Values may be adapted Python types
        (e.g. ``datetime.date``) or duck-typed relational objects that expose an
        ``ids`` attribute (e.g. ``OdooRecordset``).
        """
        if not self._nodes:
            return True
        return all(_evaluate_node(record_values, node) for node in self._nodes)

    @classmethod
    def AND(cls, iterable: Iterable[Any]) -> DomainExpression:
        """Combine an iterable of domains (expressions or raw lists) with logical AND."""
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
        """Combine an iterable of domains (expressions or raw lists) with logical OR."""
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
        """Return the logical negation of this domain."""
        if self.is_empty():
            return type(self).FALSE
        node = _to_domain_node(self)
        assert node is not None
        return type(self)((BooleanExpression("!", (node,)),))

    def __and__(self, other: Any) -> DomainExpression:
        """Return the logical AND of this domain and another."""
        return type(self).AND([self, type(self).normalize(other)])

    def __or__(self, other: Any) -> DomainExpression:
        """Return the logical OR of this domain and another."""
        return type(self).OR([self, type(self).normalize(other)])


def _to_domain_node(expr: DomainExpression) -> DomainNode | None:
    """Reduce a DomainExpression to a single node for embedding in boolean trees.

    Returns ``None`` for empty (TRUE) expressions, the sole node for single-node
    expressions, or an implicit ``&`` wrapper otherwise.
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

    Handles single conditions, flat prefix token streams, and nested groups.

    :raises ValueError: When the input shape is not a supported domain form.
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
    """Parse one boolean or condition expression from a prefix token sequence.

    :raises ValueError: When tokens end early or contain unsupported operators.
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
    """Parse a binary boolean expression (``&`` or ``|``) from a token sequence."""
    left, next_index = _parse_expression(items, index + 1)
    right, next_index = _parse_expression(items, next_index)
    return BooleanExpression(operator, (left, right)), next_index


def _parse_unary_expression(items: list[Any], index: int) -> tuple[DomainNode, int]:
    """Parse a unary boolean expression (``!``) from a token sequence."""
    operand, next_index = _parse_expression(items, index + 1)
    return BooleanExpression("!", (operand,)), next_index


def _normalize_item(item: Any) -> DomainNode:
    """Normalize one nested domain item (a condition or an implicit ``&`` group).

    :raises ValueError: When the item is not a supported domain fragment.
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
    """Create a normalized condition node from a raw triple, deep-copying the value.

    :raises ValueError: When the operator is not a supported leaf operator.
    """
    field, operator, value = condition
    if operator not in _VALID_LEAF_OPERATORS:
        raise ValueError(f"Unsupported domain operator: {operator!r}")
    return Condition(field, operator, deepcopy(value))


def _is_condition(value: Any) -> bool:
    """Return whether a value looks like a domain condition triple."""
    if not _is_sequence(value) or len(value) != 3:
        return False
    field, operator, _ = value
    return (
        isinstance(field, str)
        and field not in _BOOLEAN_OPERATORS
        and isinstance(operator, str)
    )


def _serialize_expression(node: DomainNode) -> Union[DomainCondition, list[Any]]:
    """Serialize one normalized node into an Odoo condition tuple or boolean tokens."""
    if isinstance(node, Condition):
        return node.serialize()
    if node.operator == "!":
        return ["!", *_serialize_tokens(node.operands[0])]
    return _serialize_boolean(node.operator, node.operands)


def _serialize_boolean(operator: str, operands: tuple[DomainNode, ...]) -> list[Any]:
    """Serialize a boolean node into Odoo's binary prefix token form.

    Odoo only accepts binary ``&``/``|`` tokens, so groups of more than two operands
    are re-associated into nested prefix expressions. Arity (>= 2) is already
    guaranteed by :meth:`BooleanExpression.__post_init__`.
    """
    operand_count = len(operands)
    assert operand_count >= 2
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
    """Serialize a node and wrap the result into a uniform list of domain tokens."""
    item = _serialize_expression(node)
    if isinstance(item, list):
        return item
    return [item]


# ---------------------------------------------------------------------------
# In-memory domain evaluation helpers
# ---------------------------------------------------------------------------


def extract_comparison_value(value: Any) -> Any:
    """Normalize an adapted field value to a primitive suitable for comparison.

    Duck-types relational objects that expose an ``ids`` attribute (e.g.
    ``OdooRecordset``) to a single id, a tuple of ids, or ``False`` when empty,
    without importing that class here.
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
    """Evaluate an ``=`` (or ``!=`` when *negate*) condition against a field value."""
    if domain_value is False or domain_value is None:
        result = not cmp
    else:
        result = cmp == domain_value
    return (not result) if negate else result


def _match_membership(cmp: Any, domain_value: Any, *, negate: bool) -> bool:
    """Evaluate an ``in`` (or ``not in`` when *negate*) condition against a field value."""
    if isinstance(cmp, tuple):
        result = any(v in domain_value for v in cmp)
        return (not result) if negate else result
    return (cmp not in domain_value) if negate else (cmp in domain_value)


def _match_like_pattern(
    cmp: Any, domain_value: Any, *, case_sensitive: bool, negate: bool
) -> bool:
    """Evaluate a LIKE/ILIKE (optionally negated) condition against a field value.

    Non-string operands yield *negate* (i.e. NOT LIKE matches, LIKE does not).
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

    :raises NotImplementedError: For ``child_of``/``parent_of``, which require
        server-side hierarchy information.
    :raises ValueError: For unrecognised operators.
    """
    cmp = extract_comparison_value(field_value)

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
    """Recursively evaluate a normalized domain node against record field values."""
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
    """Collect every field name referenced in leaf conditions of a domain node tree."""
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
