import unittest

from odoo_sdk.query.domain import DomainExpression


class TestDomainTrueConstant(unittest.TestCase):
    def test_true_is_domain_expression(self) -> None:
        self.assertIsInstance(DomainExpression.TRUE, DomainExpression)

    def test_true_is_empty(self) -> None:
        self.assertTrue(DomainExpression.TRUE.is_empty())

    def test_true_serializes_to_empty_list(self) -> None:
        self.assertEqual(DomainExpression.TRUE.serialize(), [])


class TestDomainFalseConstant(unittest.TestCase):
    def test_false_is_domain_expression(self) -> None:
        self.assertIsInstance(DomainExpression.FALSE, DomainExpression)

    def test_false_is_not_empty(self) -> None:
        self.assertFalse(DomainExpression.FALSE.is_empty())

    def test_false_serializes_to_id_equals_false(self) -> None:
        self.assertEqual(DomainExpression.FALSE.serialize(), [("id", "=", False)])


class TestDomainExpressionAND(unittest.TestCase):
    def setUp(self) -> None:
        self.d1 = DomainExpression.normalize([("active", "=", True)])
        self.d2 = DomainExpression.normalize([("company_id", "=", 1)])
        self.d3 = DomainExpression.normalize([("state", "=", "open")])

    def test_and_empty_iterable_returns_true(self) -> None:
        result = DomainExpression.AND([])
        self.assertEqual(result.serialize(), DomainExpression.TRUE.serialize())

    def test_and_single_item_returns_same(self) -> None:
        result = DomainExpression.AND([self.d1])
        self.assertEqual(result.serialize(), self.d1.serialize())

    def test_and_two_items_serializes_with_ampersand_prefix(self) -> None:
        result = DomainExpression.AND([self.d1, self.d2])
        self.assertEqual(
            result.serialize(),
            ["&", ("active", "=", True), ("company_id", "=", 1)],
        )

    def test_and_three_items_serializes_correctly(self) -> None:
        result = DomainExpression.AND([self.d1, self.d2, self.d3])
        serialized = result.serialize()
        # Should contain "&" prefix twice (left-associative nesting) and all three conditions
        self.assertEqual(serialized.count("&"), 2)
        self.assertIn(("active", "=", True), serialized)
        self.assertIn(("company_id", "=", 1), serialized)
        self.assertIn(("state", "=", "open"), serialized)

    def test_and_accepts_raw_list_operand(self) -> None:
        result = DomainExpression.AND([[("active", "=", True)], [("company_id", "=", 1)]])
        self.assertEqual(
            result.serialize(),
            ["&", ("active", "=", True), ("company_id", "=", 1)],
        )

    def test_and_with_true_identity_element(self) -> None:
        result = DomainExpression.AND([DomainExpression.TRUE, self.d1])
        self.assertEqual(result.serialize(), self.d1.serialize())

    def test_and_all_true_returns_true(self) -> None:
        result = DomainExpression.AND([DomainExpression.TRUE, DomainExpression.TRUE])
        self.assertEqual(result.serialize(), [])

    def test_and_returns_new_instance(self) -> None:
        result = DomainExpression.AND([self.d1, self.d2])
        self.assertIsNot(result, self.d1)
        self.assertIsNot(result, self.d2)


class TestDomainExpressionOR(unittest.TestCase):
    def setUp(self) -> None:
        self.d1 = DomainExpression.normalize([("active", "=", True)])
        self.d2 = DomainExpression.normalize([("company_id", "=", 1)])
        self.d3 = DomainExpression.normalize([("state", "=", "open")])

    def test_or_empty_iterable_returns_false(self) -> None:
        result = DomainExpression.OR([])
        self.assertEqual(result.serialize(), DomainExpression.FALSE.serialize())

    def test_or_single_item_returns_same(self) -> None:
        result = DomainExpression.OR([self.d1])
        self.assertEqual(result.serialize(), self.d1.serialize())

    def test_or_two_items_serializes_with_pipe_prefix(self) -> None:
        result = DomainExpression.OR([self.d1, self.d2])
        self.assertEqual(
            result.serialize(),
            ["|", ("active", "=", True), ("company_id", "=", 1)],
        )

    def test_or_three_items_serializes_correctly(self) -> None:
        result = DomainExpression.OR([self.d1, self.d2, self.d3])
        serialized = result.serialize()
        self.assertEqual(serialized.count("|"), 2)
        self.assertIn(("active", "=", True), serialized)
        self.assertIn(("company_id", "=", 1), serialized)
        self.assertIn(("state", "=", "open"), serialized)

    def test_or_accepts_raw_list_operand(self) -> None:
        result = DomainExpression.OR([[("active", "=", True)], [("company_id", "=", 1)]])
        self.assertEqual(
            result.serialize(),
            ["|", ("active", "=", True), ("company_id", "=", 1)],
        )

    def test_or_with_true_absorbing_element(self) -> None:
        result = DomainExpression.OR([DomainExpression.TRUE, self.d1])
        self.assertEqual(result.serialize(), [])

    def test_or_returns_new_instance(self) -> None:
        result = DomainExpression.OR([self.d1, self.d2])
        self.assertIsNot(result, self.d1)
        self.assertIsNot(result, self.d2)


class TestDomainExpressionInvert(unittest.TestCase):
    def setUp(self) -> None:
        self.d = DomainExpression.normalize([("active", "=", True)])

    def test_invert_wraps_with_not_prefix(self) -> None:
        result = ~self.d
        self.assertEqual(result.serialize(), ["!", ("active", "=", True)])

    def test_invert_returns_new_instance(self) -> None:
        result = ~self.d
        self.assertIsNot(result, self.d)

    def test_double_invert_round_trips(self) -> None:
        result = ~~self.d
        self.assertEqual(result.serialize(), ["!", "!", ("active", "=", True)])

    def test_invert_true_returns_false(self) -> None:
        result = ~DomainExpression.TRUE
        self.assertEqual(result.serialize(), DomainExpression.FALSE.serialize())

    def test_invert_false(self) -> None:
        result = ~DomainExpression.FALSE
        self.assertEqual(result.serialize(), ["!", ("id", "=", False)])

    def test_invert_multi_node_expression(self) -> None:
        multi = DomainExpression.normalize(
            ["&", ("active", "=", True), ("company_id", "=", 1)]
        )
        result = ~multi
        serialized = result.serialize()
        self.assertEqual(serialized[0], "!")


class TestDomainExpressionAndOperator(unittest.TestCase):
    def setUp(self) -> None:
        self.d1 = DomainExpression.normalize([("active", "=", True)])
        self.d2 = DomainExpression.normalize([("company_id", "=", 1)])

    def test_and_operator_matches_and_classmethod(self) -> None:
        result_op = self.d1 & self.d2
        result_cls = DomainExpression.AND([self.d1, self.d2])
        self.assertEqual(result_op.serialize(), result_cls.serialize())

    def test_and_operator_accepts_raw_list(self) -> None:
        result = self.d1 & [("company_id", "=", 1)]
        expected = DomainExpression.AND([self.d1, self.d2])
        self.assertEqual(result.serialize(), expected.serialize())

    def test_and_operator_returns_new_instance(self) -> None:
        result = self.d1 & self.d2
        self.assertIsNot(result, self.d1)
        self.assertIsNot(result, self.d2)


class TestDomainExpressionOrOperator(unittest.TestCase):
    def setUp(self) -> None:
        self.d1 = DomainExpression.normalize([("active", "=", True)])
        self.d2 = DomainExpression.normalize([("company_id", "=", 1)])

    def test_or_operator_matches_or_classmethod(self) -> None:
        result_op = self.d1 | self.d2
        result_cls = DomainExpression.OR([self.d1, self.d2])
        self.assertEqual(result_op.serialize(), result_cls.serialize())

    def test_or_operator_accepts_raw_list(self) -> None:
        result = self.d1 | [("company_id", "=", 1)]
        expected = DomainExpression.OR([self.d1, self.d2])
        self.assertEqual(result.serialize(), expected.serialize())

    def test_or_operator_returns_new_instance(self) -> None:
        result = self.d1 | self.d2
        self.assertIsNot(result, self.d1)
        self.assertIsNot(result, self.d2)


class TestDomainDynamicTimeValues(unittest.TestCase):
    def test_dynamic_time_string_serializes_intact(self) -> None:
        domain = [("create_date", ">=", "-3d +1H")]
        result = DomainExpression.normalize(domain).serialize()
        self.assertEqual(result, [("create_date", ">=", "-3d +1H")])

    def test_now_string_serializes_intact(self) -> None:
        domain = [("write_date", "<=", "now")]
        result = DomainExpression.normalize(domain).serialize()
        self.assertEqual(result, [("write_date", "<=", "now")])

    def test_today_string_serializes_intact(self) -> None:
        domain = [("date_deadline", "=", "today")]
        result = DomainExpression.normalize(domain).serialize()
        self.assertEqual(result, [("date_deadline", "=", "today")])

    def test_complex_relative_date_string_serializes_intact(self) -> None:
        domain = [("date", ">=", "=monday -1w")]
        result = DomainExpression.normalize(domain).serialize()
        self.assertEqual(result, [("date", ">=", "=monday -1w")])

    def test_dynamic_time_value_in_composed_domain(self) -> None:
        d1 = DomainExpression.normalize([("active", "=", True)])
        d2 = DomainExpression.normalize([("create_date", ">=", "-3d +1H")])
        result = (d1 & d2).serialize()
        self.assertIn(("create_date", ">=", "-3d +1H"), result)
