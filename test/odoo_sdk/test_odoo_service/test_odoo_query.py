import unittest
from unittest.mock import Mock, call
from hypothesis import given, strategies

from odoo_sdk.odoo_service.domain_expression import DomainExpression
from odoo_sdk.odoo_service.odoo_env import OdooEnv
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

    def test_count_executes_search_count(self) -> None:
        executor = Mock()
        executor.execute.return_value = 42

        query = OdooQuery(executor, "res.partner", [("active", "=", True)])
        result = query.count()

        self.assertEqual(result, 42)
        executor.execute.assert_called_once_with(
            "res.partner", "search_count", [("active", "=", True)]
        )

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

    def test_with_context_merges_onto_existing_env_context(self) -> None:
        executor = Mock()
        executor.execute.return_value = [1]
        env = OdooEnv(executor, {"lang": "en_US"})

        query = OdooQuery(executor, "res.partner", [], env=env)
        query = query.with_context({"tz": "UTC"})
        result = query.ids()

        self.assertEqual(result, [1])
        executor.execute.assert_called_once_with(
            "res.partner",
            "search",
            [],
            context={"lang": "en_US", "tz": "UTC"},
        )
