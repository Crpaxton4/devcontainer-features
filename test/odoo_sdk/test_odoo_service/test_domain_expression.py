import unittest
from dataclasses import FrozenInstanceError

from hypothesis import given, strategies

from odoo_sdk.odoo_service.domain_expression import (
    DomainExpression,
    _BooleanExpression,
    _Condition,
    _normalize_domain_nodes,
    _parse_expression,
    _serialize_boolean,
)


class TestDomainExpression(unittest.TestCase):
    def test_condition_dataclass_uses_slots_and_is_frozen(self) -> None:
        condition = _Condition("active", "=", True)

        self.assertFalse(hasattr(condition, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            condition.field = "id"  # type: ignore[misc]

    def test_normalize_none_serializes_to_empty_domain(self) -> None:
        expression = DomainExpression.normalize(None)

        self.assertTrue(expression.is_empty())
        self.assertEqual(expression.serialize(), [])

    def test_domain_expression_dataclass_uses_slots_and_is_frozen(self) -> None:
        expression = DomainExpression.normalize([("active", "=", True)])

        self.assertFalse(hasattr(expression, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            expression._nodes = ()  # type: ignore[misc]

    def test_normalize_existing_expression_returns_same_instance(self) -> None:
        expression = DomainExpression.normalize([("active", "=", True)])

        self.assertIs(DomainExpression.normalize(expression), expression)

    def test_simple_condition_list_round_trips(self) -> None:
        domain = [("active", "=", True), ("company_id", "=", 3)]

        expression = DomainExpression.normalize(domain)

        self.assertEqual(expression.serialize(), domain)

    def test_boolean_prefix_domain_serializes_deterministically(self) -> None:
        domain = [
            ("active", "=", True),
            "|",
            ("company_id", "=", 3),
            ("name", "ilike", "Acme"),
        ]

        expression = DomainExpression.normalize(domain)

        self.assertEqual(expression.serialize(), domain)
        self.assertEqual(expression.serialize(), domain)

    def test_nested_boolean_group_flattens_to_prefix_payload(self) -> None:
        domain = [
            "|",
            ("active", "=", True),
            [
                "&",
                ("company_id", "=", 3),
                ("name", "ilike", "Acme"),
            ],
        ]

        expression = DomainExpression.normalize(domain)

        self.assertEqual(
            expression.serialize(),
            [
                "|",
                ("active", "=", True),
                "&",
                ("company_id", "=", 3),
                ("name", "ilike", "Acme"),
            ],
        )

    def test_not_operator_serializes_nested_and_group(self) -> None:
        expression = DomainExpression.normalize(
            [
                "!",
                [
                    ("active", "=", True),
                    ("company_id", "=", 3),
                    ("name", "ilike", "Acme"),
                ],
            ]
        )

        self.assertEqual(
            expression.serialize(),
            [
                "!",
                "&",
                "&",
                ("active", "=", True),
                ("company_id", "=", 3),
                ("name", "ilike", "Acme"),
            ],
        )

    def test_not_operator_parses_correctly_after_leading_condition(self) -> None:
        domain = [
            ("id", "=", 1),
            "!",
            ("active", "=", True),
        ]

        expression = DomainExpression.normalize(domain)

        self.assertEqual(expression.serialize(), domain)

    def test_normalize_domain_nodes_requires_keyword_only_allow_empty(self) -> None:
        with self.assertRaises(TypeError):
            _normalize_domain_nodes([("id", "=", 1)], True)

    def test_large_domain_round_trips_without_loop_termination_errors(self) -> None:
        domain = [(f"field_{index}", "=", index) for index in range(300)]

        expression = DomainExpression.normalize(domain)

        self.assertEqual(expression.serialize(), domain)

    def test_mutating_input_after_normalization_does_not_leak(self) -> None:
        values = [1, 2]
        expression = DomainExpression.normalize([("id", "in", values)])

        values.append(3)

        self.assertEqual(expression.serialize(), [("id", "in", [1, 2])])

    def test_mutating_serialized_payload_does_not_leak(self) -> None:
        expression = DomainExpression.normalize([("id", "in", [1, 2])])

        payload = expression.serialize()
        payload[0][2].append(3)

        self.assertEqual(expression.serialize(), [("id", "in", [1, 2])])

    def test_rejects_unknown_boolean_operator(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported domain token"):
            DomainExpression.normalize(["^", ("active", "=", True), ("id", "=", 7)])

    def test_rejects_non_sequence_domain_input(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "condition or sequence of tokens"
        ):
            DomainExpression.normalize(7)

    def test_rejects_missing_boolean_operand(self) -> None:
        with self.assertRaisesRegex(ValueError, "ended before all operands"):
            DomainExpression.normalize(["|", ("active", "=", True)])

    def test_rejects_malformed_condition(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported domain token"):
            DomainExpression.normalize([["active", "="]])

    def test_rejects_empty_nested_group(self) -> None:
        with self.assertRaisesRegex(ValueError, "Nested domain groups cannot be empty"):
            DomainExpression.normalize([[ ]])

    def test_boolean_expression_rejects_invalid_operator(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported boolean operator"):
            _BooleanExpression("^", ())

    def test_boolean_expression_rejects_wrong_not_arity(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one operand"):
            _BooleanExpression("!", ())

    def test_boolean_expression_rejects_not_with_multiple_operands(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one operand"):
            _BooleanExpression(
                "!",
                (
                    _Condition("active", "=", True),
                    _Condition("company_id", "=", 3),
                ),
            )

    def test_boolean_expression_rejects_short_binary_arity(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least two operands"):
            _BooleanExpression("&", (_Condition("active", "=", True),))

    def test_boolean_expression_dataclass_uses_slots(self) -> None:
        expression = _BooleanExpression(
            "&",
            (
                _Condition("active", "=", True),
                _Condition("company_id", "=", 3),
            ),
        )

        self.assertFalse(hasattr(expression, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            expression.operator = "|"  # type: ignore[misc]

    def test_parse_expression_rejects_index_beyond_end(self) -> None:
        with self.assertRaisesRegex(ValueError, "ended before all operands"):
            _parse_expression([], 1)

    def test_parse_expression_rejects_index_at_end_for_large_runtime_integer(self) -> None:
        items = [(f"field_{index}", "=", index) for index in range(300)]
        index = int("300")

        with self.assertRaisesRegex(ValueError, "ended before all operands"):
            _parse_expression(items, index)

    def test_dynamic_not_token_is_parsed_by_value_not_identity(self) -> None:
        class DynamicBang(str):
            pass

        expression = DomainExpression.normalize(
            [DynamicBang("!"), ("active", "=", True)]
        )

        self.assertEqual(expression.serialize(), ["!", ("active", "=", True)])

    def test_serialize_boolean_rejects_single_operand_binary_operator(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least two operands"):
            _serialize_boolean("&", (_Condition("active", "=", True),))

    @given(
        strategies.text().filter(lambda text: text not in {"&", "|", "!"}),
        strategies.text(),
        strategies.integers(),
    )
    def test_single_condition_tuple_round_trips(
        self, field_name: str, operator: str, value: int
    ) -> None:
        condition = (field_name, operator, value)

        expression = DomainExpression.normalize(condition)

        self.assertEqual(expression.serialize(), [condition])
