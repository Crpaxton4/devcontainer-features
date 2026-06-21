import unittest
from datetime import date

from odoo_sdk.query.domain import (
    DomainExpression,
    _extract_comparison_value,
    _match_condition,
    _collect_domain_fields,
)


class _FakeRecordset:
    """Minimal duck-typed stand-in for OdooRecordset used in evaluator tests."""

    def __init__(self, *ids: int) -> None:
        self.ids = ids


class TestExtractComparisonValue(unittest.TestCase):
    def test_plain_scalar_passes_through(self) -> None:
        self.assertEqual(_extract_comparison_value(42), 42)
        self.assertEqual(_extract_comparison_value("hello"), "hello")
        self.assertIs(_extract_comparison_value(True), True)
        self.assertIs(_extract_comparison_value(None), None)

    def test_empty_recordset_becomes_false(self) -> None:
        rs = _FakeRecordset()
        self.assertIs(_extract_comparison_value(rs), False)

    def test_singleton_recordset_becomes_id_int(self) -> None:
        rs = _FakeRecordset(7)
        self.assertEqual(_extract_comparison_value(rs), 7)

    def test_multi_recordset_becomes_id_tuple(self) -> None:
        rs = _FakeRecordset(1, 2, 3)
        self.assertEqual(_extract_comparison_value(rs), (1, 2, 3))


class TestMatchCondition(unittest.TestCase):
    # --- equality operators ---

    def test_eq_matches_equal_scalar(self) -> None:
        self.assertTrue(_match_condition("foo", "=", "foo"))

    def test_eq_rejects_unequal_scalar(self) -> None:
        self.assertFalse(_match_condition("foo", "=", "bar"))

    def test_eq_false_operand_matches_falsy_field(self) -> None:
        self.assertTrue(_match_condition(False, "=", False))
        self.assertTrue(_match_condition(None, "=", False))
        self.assertTrue(_match_condition(0, "=", False))

    def test_eq_false_operand_rejects_truthy_field(self) -> None:
        self.assertFalse(_match_condition("something", "=", False))

    def test_eq_empty_recordset_matches_false_operand(self) -> None:
        rs = _FakeRecordset()
        self.assertTrue(_match_condition(rs, "=", False))

    def test_eq_singleton_recordset_matches_id(self) -> None:
        rs = _FakeRecordset(5)
        self.assertTrue(_match_condition(rs, "=", 5))
        self.assertFalse(_match_condition(rs, "=", 9))

    def test_neq_opposite_of_eq(self) -> None:
        self.assertTrue(_match_condition("foo", "!=", "bar"))
        self.assertFalse(_match_condition("foo", "!=", "foo"))

    def test_neq_false_operand_matches_truthy(self) -> None:
        self.assertTrue(_match_condition("something", "!=", False))

    # --- comparison operators ---

    def test_lt_gt_le_ge_integers(self) -> None:
        self.assertTrue(_match_condition(3, "<", 5))
        self.assertFalse(_match_condition(5, "<", 3))
        self.assertTrue(_match_condition(5, ">", 3))
        self.assertFalse(_match_condition(3, ">", 5))
        self.assertTrue(_match_condition(3, "<=", 3))
        self.assertTrue(_match_condition(3, "<=", 5))
        self.assertTrue(_match_condition(5, ">=", 5))
        self.assertTrue(_match_condition(5, ">=", 3))

    def test_comparison_operators_on_dates(self) -> None:
        d1 = date(2023, 1, 1)
        d2 = date(2023, 6, 1)
        self.assertTrue(_match_condition(d1, "<", d2))
        self.assertFalse(_match_condition(d2, "<", d1))
        self.assertTrue(_match_condition(d2, ">", d1))

    # --- in / not in ---

    def test_in_scalar_true_when_present(self) -> None:
        self.assertTrue(_match_condition(2, "in", [1, 2, 3]))

    def test_in_scalar_false_when_absent(self) -> None:
        self.assertFalse(_match_condition(9, "in", [1, 2, 3]))

    def test_not_in_scalar(self) -> None:
        self.assertTrue(_match_condition(9, "not in", [1, 2, 3]))
        self.assertFalse(_match_condition(2, "not in", [1, 2, 3]))

    def test_in_with_x2many_recordset(self) -> None:
        rs = _FakeRecordset(1, 3)
        self.assertTrue(_match_condition(rs, "in", [1, 2]))
        self.assertFalse(_match_condition(rs, "in", [5, 6]))

    def test_not_in_with_x2many_recordset(self) -> None:
        rs = _FakeRecordset(1, 3)
        self.assertFalse(_match_condition(rs, "not in", [1, 2]))
        self.assertTrue(_match_condition(rs, "not in", [5, 6]))

    # --- like / ilike ---

    def test_like_matches_exact_string(self) -> None:
        self.assertTrue(_match_condition("hello", "like", "hello"))
        self.assertFalse(_match_condition("hello", "like", "HELLO"))

    def test_like_with_percent_wildcard(self) -> None:
        self.assertTrue(_match_condition("hello world", "like", "hello%"))
        self.assertTrue(_match_condition("say hello", "like", "%hello"))
        self.assertTrue(_match_condition("say hello world", "like", "%hello%"))
        self.assertFalse(_match_condition("goodbye", "like", "hello%"))

    def test_like_with_underscore_wildcard(self) -> None:
        self.assertTrue(_match_condition("abc", "like", "a_c"))
        self.assertFalse(_match_condition("abbc", "like", "a_c"))

    def test_ilike_is_case_insensitive(self) -> None:
        self.assertTrue(_match_condition("Hello", "ilike", "hello"))
        self.assertTrue(_match_condition("HELLO WORLD", "ilike", "%world"))
        self.assertFalse(_match_condition("goodbye", "ilike", "hello"))

    def test_eq_like_is_case_sensitive_like(self) -> None:
        self.assertTrue(_match_condition("hello", "=like", "hello%"))
        self.assertFalse(_match_condition("HELLO", "=like", "hello%"))

    def test_eq_ilike_is_case_insensitive_like(self) -> None:
        self.assertTrue(_match_condition("HELLO", "=ilike", "hello%"))

    def test_not_like_negates_like(self) -> None:
        self.assertFalse(_match_condition("hello", "not like", "hello%"))
        self.assertTrue(_match_condition("goodbye", "not like", "hello%"))

    def test_not_ilike_negates_ilike(self) -> None:
        self.assertFalse(_match_condition("HELLO", "not ilike", "hello%"))
        self.assertTrue(_match_condition("goodbye", "not ilike", "hello%"))

    def test_like_non_string_field_returns_false(self) -> None:
        self.assertFalse(_match_condition(42, "like", "4%"))

    def test_not_like_non_string_field_returns_true(self) -> None:
        self.assertTrue(_match_condition(42, "not like", "4%"))

    # --- error cases ---

    def test_unsupported_operator_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            _match_condition("x", "~~", "x")

    def test_child_of_raises_not_implemented(self) -> None:
        with self.assertRaises(NotImplementedError):
            _match_condition(1, "child_of", 2)

    def test_parent_of_raises_not_implemented(self) -> None:
        with self.assertRaises(NotImplementedError):
            _match_condition(1, "parent_of", 2)


class TestDomainExpressionMatches(unittest.TestCase):
    def test_empty_domain_always_matches(self) -> None:
        expr = DomainExpression.normalize([])
        self.assertTrue(expr.matches({}))
        self.assertTrue(expr.matches({"active": False}))

    def test_single_condition_true(self) -> None:
        expr = DomainExpression.normalize([("active", "=", True)])
        self.assertTrue(expr.matches({"active": True}))

    def test_single_condition_false(self) -> None:
        expr = DomainExpression.normalize([("active", "=", True)])
        self.assertFalse(expr.matches({"active": False}))

    def test_missing_field_treated_as_none(self) -> None:
        expr = DomainExpression.normalize([("active", "=", False)])
        self.assertTrue(expr.matches({}))

    def test_implicit_and_of_two_conditions(self) -> None:
        expr = DomainExpression.normalize(
            [("active", "=", True), ("state", "=", "draft")]
        )
        self.assertTrue(expr.matches({"active": True, "state": "draft"}))
        self.assertFalse(expr.matches({"active": True, "state": "done"}))
        self.assertFalse(expr.matches({"active": False, "state": "draft"}))

    def test_explicit_and_operator(self) -> None:
        expr = DomainExpression.normalize(
            ["&", ("active", "=", True), ("state", "=", "draft")]
        )
        self.assertTrue(expr.matches({"active": True, "state": "draft"}))
        self.assertFalse(expr.matches({"active": False, "state": "draft"}))

    def test_or_operator(self) -> None:
        expr = DomainExpression.normalize(
            ["|", ("active", "=", True), ("state", "=", "draft")]
        )
        self.assertTrue(expr.matches({"active": True, "state": "done"}))
        self.assertTrue(expr.matches({"active": False, "state": "draft"}))
        self.assertFalse(expr.matches({"active": False, "state": "done"}))

    def test_not_operator(self) -> None:
        expr = DomainExpression.normalize(["!", ("active", "=", True)])
        self.assertTrue(expr.matches({"active": False}))
        self.assertFalse(expr.matches({"active": True}))

    def test_nested_boolean_expression(self) -> None:
        # active=True AND (state=draft OR state=confirm)
        expr = DomainExpression.normalize(
            [
                "&",
                ("active", "=", True),
                "|",
                ("state", "=", "draft"),
                ("state", "=", "confirm"),
            ]
        )
        self.assertTrue(expr.matches({"active": True, "state": "draft"}))
        self.assertTrue(expr.matches({"active": True, "state": "confirm"}))
        self.assertFalse(expr.matches({"active": True, "state": "done"}))
        self.assertFalse(expr.matches({"active": False, "state": "draft"}))

    def test_many2one_id_comparison(self) -> None:
        rs = _FakeRecordset(3)
        expr = DomainExpression.normalize([("partner_id", "=", 3)])
        self.assertTrue(expr.matches({"partner_id": rs}))
        expr2 = DomainExpression.normalize([("partner_id", "=", 9)])
        self.assertFalse(expr2.matches({"partner_id": rs}))

    def test_many2one_false_matches_empty_recordset(self) -> None:
        rs = _FakeRecordset()
        expr = DomainExpression.normalize([("partner_id", "=", False)])
        self.assertTrue(expr.matches({"partner_id": rs}))


class TestCollectDomainFields(unittest.TestCase):
    def test_collects_fields_from_flat_conditions(self) -> None:
        expr = DomainExpression.normalize(
            [("active", "=", True), ("state", "=", "draft")]
        )
        self.assertEqual(expr.field_names(), {"active", "state"})

    def test_collects_fields_from_nested_boolean(self) -> None:
        expr = DomainExpression.normalize(
            [
                "&",
                ("active", "=", True),
                "|",
                ("state", "=", "draft"),
                ("amount_total", ">", 0),
            ]
        )
        self.assertEqual(expr.field_names(), {"active", "state", "amount_total"})

    def test_empty_domain_returns_empty_set(self) -> None:
        expr = DomainExpression.normalize([])
        self.assertEqual(expr.field_names(), set())

    def test_not_operator_includes_nested_field(self) -> None:
        expr = DomainExpression.normalize(["!", ("active", "=", True)])
        self.assertEqual(expr.field_names(), {"active"})
