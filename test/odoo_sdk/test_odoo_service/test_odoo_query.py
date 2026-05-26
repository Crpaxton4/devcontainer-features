import unittest
from unittest.mock import Mock, call
from hypothesis import given, strategies

from odoo_sdk.odoo_service import OdooValidationError, X2ManyCommand
from odoo_sdk.odoo_service.field_values import RelationValue
from odoo_sdk.odoo_service.domain_expression import DomainExpression
from odoo_sdk.odoo_service.odoo_query import OdooQuery


class TestOdooQueryWrite(unittest.TestCase):

    @given(
        strategies.text(), strategies.dictionaries(strategies.text(), strategies.text())
    )
    def test_write_executes_search_then_write(
        self, model_name: str, update_data: dict[str, str]
    ) -> None:
        executor = Mock()
        executor.execute.side_effect = [[1, 2, 3], True]

        query = OdooQuery(executor, model_name, [("active", "=", True)])

        result = query.write(update_data)

        self.assertTrue(result)
        self.assertEqual(
            executor.execute.call_args_list,
            [
                call(model_name, "search", [("active", "=", True)]),
                call(model_name, "write", [1, 2, 3], update_data),
            ],
        )

    @given(strategies.text())
    def test_unlink_executes_search_then_unlink(self, model_name: str) -> None:
        executor = Mock()
        executor.execute.side_effect = [[1, 2, 3], True]

        query = OdooQuery(executor, model_name, [("active", "=", False)])

        result = query.unlink()

        self.assertTrue(result)
        self.assertEqual(
            executor.execute.call_args_list,
            [
                call(model_name, "search", [("active", "=", False)]),
                call(model_name, "unlink", [1, 2, 3]),
            ],
        )

    def test_ids_propagates_sdk_error_without_wrapping(self) -> None:
        executor = Mock()
        error = OdooValidationError(
            "Odoo validation failed (res.partner.search)",
            operation="res.partner.search",
            model="res.partner",
            method="search",
        )
        executor.execute.side_effect = error

        query = OdooQuery(executor, "res.partner", [("active", "=", True)])

        with self.assertRaises(OdooValidationError) as caught:
            query.ids()

        self.assertIs(caught.exception, error)

    def test_chained_queries_are_independent_instances(self) -> None:
        executor = Mock()

        base = OdooQuery(executor, "res.partner", [])

        query1 = base.search([("active", "=", True)]).limit(10)
        query2 = base.search([("active", "=", False)]).limit(5)

        self.assertIsNot(base, query1)
        self.assertIsNot(base, query2)
        self.assertIsNot(query1, query2)

        self.assertEqual(
            query1._domain,
            DomainExpression.normalize([("active", "=", True)]),
        )
        self.assertEqual(query1._limit, 10)

        self.assertEqual(
            query2._domain,
            DomainExpression.normalize([("active", "=", False)]),
        )
        self.assertEqual(query2._limit, 5)

        self.assertEqual(base._domain, DomainExpression.normalize([]))
        self.assertIsNone(base._limit)

    def test_builder_methods_return_new_queries_without_mutating_source(self) -> None:
        executor = Mock()

        base = OdooQuery(executor, "res.partner", [("active", "=", True)])

        with_limit = base.limit(10)
        with_offset = base.offset(5)
        with_order = base.order_by("name asc")
        with_context = base.with_context({"lang": "en_US"})
        replaced_domain = base.search([("company_id", "=", 3)])

        self.assertIsNot(base, with_limit)
        self.assertIsNot(base, with_offset)
        self.assertIsNot(base, with_order)
        self.assertIsNot(base, with_context)
        self.assertIsNot(base, replaced_domain)

        self.assertEqual(base._domain, DomainExpression.normalize([("active", "=", True)]))
        self.assertIsNone(base._limit)
        self.assertIsNone(base._offset)
        self.assertIsNone(base._order)
        self.assertEqual(base._env.context, {})

        self.assertEqual(with_limit._limit, 10)
        self.assertIsNone(with_limit._offset)
        self.assertIsNone(with_limit._order)

        self.assertIsNone(with_offset._limit)
        self.assertEqual(with_offset._offset, 5)
        self.assertIsNone(with_offset._order)

        self.assertIsNone(with_order._limit)
        self.assertIsNone(with_order._offset)
        self.assertEqual(with_order._order, "name asc")

        self.assertEqual(with_context._env.context, {"lang": "en_US"})
        self.assertEqual(
            replaced_domain._domain,
            DomainExpression.normalize([("company_id", "=", 3)]),
        )

    def test_with_context_does_not_mutate_previously_created_queries(self) -> None:
        executor = Mock()
        executor.execute.side_effect = [[1], [2]]

        base = OdooQuery(executor, "res.partner", [])
        derived = base.with_context({"lang": "en_US"})

        self.assertEqual(base.ids(), [1])
        self.assertEqual(derived.ids(), [2])
        self.assertEqual(
            executor.execute.call_args_list,
            [
                call("res.partner", "search", []),
                call("res.partner", "search", [], context={"lang": "en_US"}),
            ],
        )

    def test_boolean_prefix_domain_is_serialized_before_execution(self) -> None:
        executor = Mock()
        executor.execute.return_value = [7, 8]

        query = OdooQuery(
            executor,
            "res.partner",
            [
                ("active", "=", True),
                "|",
                ("company_id", "=", 3),
                ("name", "ilike", "Acme"),
            ],
        )

        result = query.ids()

        self.assertEqual(result, [7, 8])
        executor.execute.assert_called_once_with(
            "res.partner",
            "search",
            [
                ("active", "=", True),
                "|",
                ("company_id", "=", 3),
                ("name", "ilike", "Acme"),
            ],
        )

    def test_read_executes_search_read_with_pagination_and_fields(self) -> None:
        executor = Mock()
        executor.execute.return_value = [{"id": 8, "name": "Acme"}]

        query = OdooQuery(executor, "res.partner", [("active", "=", True)])
        result = query.limit(5).offset(10).read(["name"])

        self.assertEqual(result, [{"id": 8, "name": "Acme"}])
        executor.execute.assert_called_once_with(
            "res.partner",
            "search_read",
            [("active", "=", True)],
            limit=5,
            offset=10,
            fields=["name"],
        )

    def test_read_delegates_to_recordset_search_read(self) -> None:
        executor = Mock()
        recordset = Mock()
        recordset.search_read.return_value = [{"id": 8, "name": "Acme"}]

        query = (
            OdooQuery(executor, "res.partner", [("active", "=", True)])
            .limit(5)
            .offset(10)
            .order_by("name asc")
            .with_context({"lang": "en_US"})
        )
        query._recordset = Mock(return_value=recordset)

        result = query.read(["name"])

        self.assertEqual(result, [{"id": 8, "name": "Acme"}])
        query._recordset.assert_called_once_with()
        recordset.search_read.assert_called_once_with(
            DomainExpression.normalize([("active", "=", True)]),
            fields=["name"],
            limit=5,
            offset=10,
            order="name asc",
        )
        executor.execute.assert_not_called()

    def test_read_adapted_delegates_to_recordset_search_read_adapted(self) -> None:
        executor = Mock()
        recordset = Mock()
        recordset.search_read_adapted.return_value = [
            {"id": 8, "parent_id": RelationValue("res.partner", 7, "Parent")}
        ]

        query = (
            OdooQuery(executor, "res.partner", [("active", "=", True)])
            .limit(5)
            .offset(10)
            .order_by("name asc")
            .with_context({"lang": "en_US"})
        )
        query._recordset = Mock(return_value=recordset)

        result = query.read_adapted(["parent_id"])

        self.assertEqual(
            result,
            [{"id": 8, "parent_id": RelationValue("res.partner", 7, "Parent")}],
        )
        query._recordset.assert_called_once_with()
        recordset.search_read_adapted.assert_called_once_with(
            DomainExpression.normalize([("active", "=", True)]),
            fields=["parent_id"],
            limit=5,
            offset=10,
            order="name asc",
        )
        executor.execute.assert_not_called()

    def test_read_raw_and_adapted_behaviors_remain_explicit(self) -> None:
        executor = Mock()
        executor.execute.side_effect = [
            [{"id": 1, "parent_id": [7, "Parent"]}],
            [{"id": 1, "parent_id": [7, "Parent"]}],
            {"parent_id": {"type": "many2one", "relation": "res.partner"}},
        ]

        query = OdooQuery(executor, "res.partner", [("id", "=", 1)])

        raw = query.read(["parent_id"])
        adapted = query.read_adapted(["parent_id"])

        self.assertEqual(raw, [{"id": 1, "parent_id": [7, "Parent"]}])
        self.assertEqual(
            adapted,
            [{"id": 1, "parent_id": RelationValue("res.partner", 7, "Parent")}],
        )

    def test_count_executes_search_count(self) -> None:
        executor = Mock()
        executor.execute.return_value = 42

        query = OdooQuery(executor, "res.partner", [("active", "=", True)])
        result = query.count()

        self.assertEqual(result, 42)
        executor.execute.assert_called_once_with(
            "res.partner", "search_count", [("active", "=", True)]
        )

    def test_count_delegates_to_recordset_search_count(self) -> None:
        executor = Mock()
        recordset = Mock()
        recordset.search_count.return_value = 42

        query = OdooQuery(executor, "res.partner", [("active", "=", True)])
        query._recordset = Mock(return_value=recordset)

        result = query.count()

        self.assertEqual(result, 42)
        query._recordset.assert_called_once_with()
        recordset.search_count.assert_called_once_with(
            DomainExpression.normalize([("active", "=", True)])
        )
        executor.execute.assert_not_called()

    def test_ids_delegates_to_recordset_search_ids(self) -> None:
        executor = Mock()
        recordset = Mock()
        recordset.search_ids.return_value = [7, 8]

        query = (
            OdooQuery(executor, "res.partner", [("active", "=", True)])
            .limit(5)
            .offset(1)
            .order_by("name asc")
        )
        query._recordset = Mock(return_value=recordset)

        result = query.ids()

        self.assertEqual(result, [7, 8])
        query._recordset.assert_called_once_with()
        recordset.search_ids.assert_called_once_with(
            DomainExpression.normalize([("active", "=", True)]),
            limit=5,
            offset=1,
            order="name asc",
        )
        executor.execute.assert_not_called()

    def test_ids_executes_search_with_order_and_context(self) -> None:
        executor = Mock()
        executor.execute.return_value = [7, 8]

        query = OdooQuery(executor, "res.partner", [("active", "=", True)])
        result = query.order_by("name asc").with_context({"lang": "en_US"}).ids()

        self.assertEqual(result, [7, 8])
        executor.execute.assert_called_once_with(
            "res.partner",
            "search",
            [("active", "=", True)],
            order="name asc",
            context={"lang": "en_US"},
        )

    def test_read_passes_order_and_context(self) -> None:
        executor = Mock()
        executor.execute.return_value = [{"id": 1, "name": "Acme"}]

        query = OdooQuery(executor, "res.partner", [("active", "=", True)])
        result = (
            query.order_by("name asc")
            .with_context({"lang": "en_US"})
            .limit(3)
            .read(["name"])
        )

        self.assertEqual(result, [{"id": 1, "name": "Acme"}])
        executor.execute.assert_called_once_with(
            "res.partner",
            "search_read",
            [("active", "=", True)],
            limit=3,
            order="name asc",
            context={"lang": "en_US"},
            fields=["name"],
        )

    def test_count_passes_context(self) -> None:
        executor = Mock()
        executor.execute.return_value = 9

        query = OdooQuery(executor, "res.partner", [("active", "=", True)])
        result = query.with_context({"active_test": False}).count()

        self.assertEqual(result, 9)
        executor.execute.assert_called_once_with(
            "res.partner",
            "search_count",
            [("active", "=", True)],
            context={"active_test": False},
        )

    def test_write_passes_search_options_and_context(self) -> None:
        executor = Mock()
        executor.execute.side_effect = [[3, 4], True]

        query = OdooQuery(executor, "res.partner", [("active", "=", True)])
        result = (
            query.order_by("name asc")
            .limit(2)
            .with_context({"lang": "en_US"})
            .write({"comment": "Updated"})
        )

        self.assertTrue(result)
        self.assertEqual(
            executor.execute.call_args_list,
            [
                call(
                    "res.partner",
                    "search",
                    [("active", "=", True)],
                    limit=2,
                    order="name asc",
                    context={"lang": "en_US"},
                ),
                call(
                    "res.partner",
                    "write",
                    [3, 4],
                    {"comment": "Updated"},
                    context={"lang": "en_US"},
                ),
            ],
        )

    def test_write_serializes_x2many_helpers_through_recordset_path(self) -> None:
        executor = Mock()
        executor.execute.side_effect = [
            [3, 4],
            {"tag_ids": {"type": "many2many"}},
            True,
        ]

        query = OdooQuery(executor, "res.partner", [("active", "=", True)])
        result = query.with_context({"lang": "en_US"}).write(
            {"tag_ids": [X2ManyCommand.link(9), (5,)]}
        )

        self.assertTrue(result)
        self.assertEqual(
            executor.execute.call_args_list,
            [
                call(
                    "res.partner",
                    "search",
                    [("active", "=", True)],
                    context={"lang": "en_US"},
                ),
                call(
                    "res.partner",
                    "fields_get",
                    allfields=["tag_ids"],
                    attributes=["type"],
                    context={"lang": "en_US"},
                ),
                call(
                    "res.partner",
                    "write",
                    [3, 4],
                    {"tag_ids": [(4, 9, 0), (5, 0, 0)]},
                    context={"lang": "en_US"},
                ),
            ],
        )

    def test_write_delegates_to_recordset_search_write(self) -> None:
        executor = Mock()
        recordset = Mock()
        recordset.search_write.return_value = True

        query = OdooQuery(executor, "res.partner", [("active", "=", True)])
        query._recordset = Mock(return_value=recordset)

        result = query.write({})

        self.assertTrue(result)
        query._recordset.assert_called_once_with()
        recordset.search_write.assert_called_once_with(
            DomainExpression.normalize([("active", "=", True)]),
            {},
            limit=None,
            offset=None,
            order=None,
        )
        executor.execute.assert_not_called()

    def test_write_keeps_empty_search_and_empty_values_compatible(self) -> None:
        executor = Mock()
        executor.execute.side_effect = [[], True]

        query = OdooQuery(executor, "res.partner", [("active", "=", True)])
        result = query.with_context({"lang": "en_US"}).write({})

        self.assertTrue(result)
        self.assertEqual(
            executor.execute.call_args_list,
            [
                call(
                    "res.partner",
                    "search",
                    [("active", "=", True)],
                    context={"lang": "en_US"},
                ),
                call(
                    "res.partner",
                    "write",
                    [],
                    {},
                    context={"lang": "en_US"},
                ),
            ],
        )

    def test_unlink_passes_search_options_and_context(self) -> None:
        executor = Mock()
        executor.execute.side_effect = [[3, 4], True]

        query = OdooQuery(executor, "res.partner", [("active", "=", True)])
        result = query.offset(1).with_context({"lang": "en_US"}).unlink()

        self.assertTrue(result)
        self.assertEqual(
            executor.execute.call_args_list,
            [
                call(
                    "res.partner",
                    "search",
                    [("active", "=", True)],
                    offset=1,
                    context={"lang": "en_US"},
                ),
                call("res.partner", "unlink", [3, 4], context={"lang": "en_US"}),
            ],
        )

    def test_unlink_delegates_to_recordset_search_unlink(self) -> None:
        executor = Mock()
        recordset = Mock()
        recordset.search_unlink.return_value = True

        query = OdooQuery(executor, "res.partner", [("active", "=", True)])
        query._recordset = Mock(return_value=recordset)

        result = query.unlink()

        self.assertTrue(result)
        query._recordset.assert_called_once_with()
        recordset.search_unlink.assert_called_once_with(
            DomainExpression.normalize([("active", "=", True)]),
            limit=None,
            offset=None,
            order=None,
        )
        executor.execute.assert_not_called()

    def test_unlink_keeps_empty_search_compatible(self) -> None:
        executor = Mock()
        executor.execute.side_effect = [[], True]

        query = OdooQuery(executor, "res.partner", [("active", "=", True)])
        result = query.with_context({"lang": "en_US"}).unlink()

        self.assertTrue(result)
        self.assertEqual(
            executor.execute.call_args_list,
            [
                call(
                    "res.partner",
                    "search",
                    [("active", "=", True)],
                    context={"lang": "en_US"},
                ),
                call("res.partner", "unlink", [], context={"lang": "en_US"}),
            ],
        )

    def test_with_context_merges_values(self) -> None:
        executor = Mock()
        executor.execute.return_value = [1]

        query = OdooQuery(executor, "res.partner", [])
        query = query.with_context({"lang": "en_US"}).with_context({"tz": "UTC"})
        query.ids()

        executor.execute.assert_called_once_with(
            "res.partner",
            "search",
            [],
            context={"lang": "en_US", "tz": "UTC"},
        )
