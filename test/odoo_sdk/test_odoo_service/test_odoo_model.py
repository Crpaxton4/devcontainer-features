import unittest
from unittest.mock import Mock

from odoo_sdk.odoo_service.domain_expression import DomainExpression
from odoo_sdk.odoo_service.odoo_env import OdooEnv
from odoo_sdk.odoo_service.odoo_model import OdooModel
from odoo_sdk.odoo_service.odoo_query import OdooQuery


class TestOdooModel(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = Mock()
        self.model = OdooModel(self.executor, "res.partner")

    def test_search_returns_query_builder(self) -> None:
        query = self.model.search([("active", "=", True)])
        self.assertIsInstance(query, OdooQuery)
        self.assertEqual(query._domain, DomainExpression.normalize([("active", "=", True)]))

    def test_read_accepts_single_id(self) -> None:
        self.executor.execute.return_value = [{"id": 7, "name": "Acme"}]

        result = self.model.read(7, ["name"])

        self.assertEqual(result, [{"id": 7, "name": "Acme"}])
        self.executor.execute.assert_called_once_with(
            "res.partner", "read", [7], fields=["name"]
        )

    def test_create_delegates_to_executor(self) -> None:
        self.executor.execute.return_value = 101

        result = self.model.create({"name": "New"})

        self.assertEqual(result, 101)
        self.executor.execute.assert_called_once_with(
            "res.partner", "create", {"name": "New"}
        )

    def test_write_normalizes_single_id(self) -> None:
        self.executor.execute.return_value = True

        result = self.model.write(9, {"name": "Updated"})

        self.assertTrue(result)
        self.executor.execute.assert_called_once_with(
            "res.partner", "write", [9], {"name": "Updated"}
        )

    def test_write_rejects_empty_inputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one id"):
            self.model.write([], {"name": "Updated"})

        with self.assertRaisesRegex(ValueError, "at least one value"):
            self.model.write([1], {})

    def test_unlink_normalizes_single_id(self) -> None:
        self.executor.execute.return_value = True

        result = self.model.unlink(10)

        self.assertTrue(result)
        self.executor.execute.assert_called_once_with("res.partner", "unlink", [10])

    def test_unlink_rejects_empty_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one id"):
            self.model.unlink([])

    def test_fields_get_uses_keywords(self) -> None:
        self.executor.execute.return_value = {"name": {"type": "char"}}

        result = self.model.fields_get(["name"], ["type"])

        self.assertEqual(result, {"name": {"type": "char"}})
        self.executor.execute.assert_called_once_with(
            "res.partner", "fields_get", allfields=["name"], attributes=["type"]
        )

    def test_browse_reads_ids(self) -> None:
        self.executor.execute.return_value = [{"id": 3, "name": "Demo"}]

        result = self.model.browse(3, ["name"])

        self.assertEqual(result, [{"id": 3, "name": "Demo"}])
        self.executor.execute.assert_called_once_with(
            "res.partner", "read", [3], fields=["name"]
        )

    def test_browse_delegates_to_recordset_read(self) -> None:
        recordset = Mock()
        recordset.read.return_value = [{"id": 3, "name": "Demo"}]
        self.model._recordset = Mock(return_value=recordset)

        result = self.model.browse(3, ["name"])

        self.assertEqual(result, [{"id": 3, "name": "Demo"}])
        self.model._recordset.assert_called_once_with(3)
        recordset.read.assert_called_once_with(["name"])
        self.executor.execute.assert_not_called()

    def test_search_ids_supports_pagination(self) -> None:
        self.executor.execute.return_value = [1, 2, 3]

        result = self.model.search_ids(
            [("is_company", "=", True)], limit=2, offset=5, order="name"
        )

        self.assertEqual(result, [1, 2, 3])
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "search",
            [("is_company", "=", True)],
            limit=2,
            offset=5,
            order="name",
        )

    def test_search_ids_supports_context(self) -> None:
        self.executor.execute.return_value = [1, 2]

        result = self.model.search_ids(
            [("is_company", "=", True)], context={"lang": "en_US"}
        )

        self.assertEqual(result, [1, 2])
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "search",
            [("is_company", "=", True)],
            context={"lang": "en_US"},
        )

    def test_search_ids_delegates_to_query_builder(self) -> None:
        query = Mock(spec=OdooQuery)
        query.limit.return_value = query
        query.offset.return_value = query
        query.order_by.return_value = query
        query.with_context.return_value = query
        query.ids.return_value = [1, 2]
        self.model.search = Mock(return_value=query)

        result = self.model.search_ids(
            [("is_company", "=", True)],
            limit=2,
            offset=5,
            order="name",
            context={"lang": "en_US"},
        )

        self.assertEqual(result, [1, 2])
        self.model.search.assert_called_once_with([("is_company", "=", True)])
        query.limit.assert_called_once_with(2)
        query.offset.assert_called_once_with(5)
        query.order_by.assert_called_once_with("name")
        query.with_context.assert_called_once_with({"lang": "en_US"})
        query.ids.assert_called_once_with()
        self.executor.execute.assert_not_called()

    def test_env_bound_model_read_uses_env_context(self) -> None:
        self.executor.execute.return_value = [{"id": 3, "name": "Demo"}]
        model = OdooEnv(self.executor, {"lang": "en_US"})["res.partner"]

        result = model.read(3, ["name"])

        self.assertEqual(result, [{"id": 3, "name": "Demo"}])
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "read",
            [3],
            context={"lang": "en_US"},
            fields=["name"],
        )

    def test_env_bound_model_search_read_uses_env_context(self) -> None:
        self.executor.execute.return_value = [{"id": 1, "name": "Acme"}]
        model = OdooEnv(self.executor, {"lang": "en_US"})["res.partner"]

        result = model.search_read([("is_company", "=", True)], ["name"])

        self.assertEqual(result, [{"id": 1, "name": "Acme"}])
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "search_read",
            [("is_company", "=", True)],
            fields=["name"],
            context={"lang": "en_US"},
        )

    def test_exists_returns_existing_ids_in_input_order(self) -> None:
        self.executor.execute.return_value = [2, 1]

        result = self.model.exists([3, 1, 2])

        self.assertEqual(result, [1, 2])
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "search",
            [("id", "in", [3, 1, 2])],
        )

    def test_exists_returns_empty_for_empty_input(self) -> None:
        result = self.model.exists([])
        self.assertEqual(result, [])
        self.executor.execute.assert_not_called()

    def test_exists_delegates_to_recordset_exists(self) -> None:
        existing_recordset = Mock(ids=(1, 2))
        recordset = Mock()
        recordset.exists.return_value = existing_recordset
        self.model._recordset = Mock(return_value=recordset)

        result = self.model.exists([3, 1, 2])

        self.assertEqual(result, [1, 2])
        self.model._recordset.assert_called_once_with([3, 1, 2])
        recordset.exists.assert_called_once_with()
        self.executor.execute.assert_not_called()

    def test_search_read_executes_with_fields(self) -> None:
        self.executor.execute.return_value = [{"id": 1, "name": "Acme"}]

        result = self.model.search_read(
            [("is_company", "=", True)], ["name"], limit=1, offset=0
        )

        self.assertEqual(result, [{"id": 1, "name": "Acme"}])
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "search_read",
            [("is_company", "=", True)],
            fields=["name"],
            limit=1,
            offset=0,
        )

    def test_search_read_delegates_to_query_read(self) -> None:
        query = Mock(spec=OdooQuery)
        query.limit.return_value = query
        query.offset.return_value = query
        query.order_by.return_value = query
        query.read.return_value = [{"id": 1, "name": "Acme"}]
        self.model.search = Mock(return_value=query)

        result = self.model.search_read(
            [("is_company", "=", True)],
            ["name"],
            limit=1,
            offset=0,
            order="name",
        )

        self.assertEqual(result, [{"id": 1, "name": "Acme"}])
        self.model.search.assert_called_once_with([("is_company", "=", True)])
        query.limit.assert_called_once_with(1)
        query.offset.assert_called_once_with(0)
        query.order_by.assert_called_once_with("name")
        query.read.assert_called_once_with(["name"])
        self.executor.execute.assert_not_called()

    def test_search_count_executes(self) -> None:
        self.executor.execute.return_value = 19

        result = self.model.search_count([("is_company", "=", True)])

        self.assertEqual(result, 19)
        self.executor.execute.assert_called_once_with(
            "res.partner", "search_count", [("is_company", "=", True)]
        )

    def test_search_count_delegates_to_query_count(self) -> None:
        query = Mock(spec=OdooQuery)
        query.count.return_value = 19
        self.model.search = Mock(return_value=query)

        result = self.model.search_count([("is_company", "=", True)])

        self.assertEqual(result, 19)
        self.model.search.assert_called_once_with([("is_company", "=", True)])
        query.count.assert_called_once_with()
        self.executor.execute.assert_not_called()

    def test_name_search_executes(self) -> None:
        self.executor.execute.return_value = [[1, "Acme"]]

        result = self.model.name_search(
            "ac", [("active", "=", True)], operator="ilike", limit=5
        )

        self.assertEqual(result, [[1, "Acme"]])
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "name_search",
            "ac",
            args=[("active", "=", True)],
            operator="ilike",
            limit=5,
        )

    def test_name_search_serializes_boolean_prefix_domain(self) -> None:
        self.executor.execute.return_value = [[1, "Acme"]]

        result = self.model.name_search(
            "ac",
            [
                ("active", "=", True),
                "|",
                ("company_id", "=", 3),
                ("name", "ilike", "Acme"),
            ],
            operator="ilike",
            limit=5,
        )

        self.assertEqual(result, [[1, "Acme"]])
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "name_search",
            "ac",
            args=[
                ("active", "=", True),
                "|",
                ("company_id", "=", 3),
                ("name", "ilike", "Acme"),
            ],
            operator="ilike",
            limit=5,
        )

    def test_name_get_executes(self) -> None:
        self.executor.execute.return_value = [[7, "Acme"]]

        result = self.model.name_get(7)

        self.assertEqual(result, [[7, "Acme"]])
        self.executor.execute.assert_called_once_with("res.partner", "name_get", [7])

    def test_name_get_rejects_empty_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one id"):
            self.model.name_get([])

    def test_default_get_executes(self) -> None:
        self.executor.execute.return_value = {"active": True}

        result = self.model.default_get(["active"])

        self.assertEqual(result, {"active": True})
        self.executor.execute.assert_called_once_with(
            "res.partner", "default_get", ["active"]
        )

    def test_copy_executes(self) -> None:
        self.executor.execute.return_value = 88

        result = self.model.copy(7, {"name": "Copy"})

        self.assertEqual(result, 88)
        self.executor.execute.assert_called_once_with(
            "res.partner", "copy", 7, {"name": "Copy"}
        )

    def test_read_group_executes(self) -> None:
        self.executor.execute.return_value = [{"country_id": (1, "Belgium")}]

        result = self.model.read_group(
            [("is_company", "=", True)],
            ["country_id"],
            ["country_id"],
            offset=10,
            limit=5,
            orderby="country_id",
            lazy=False,
        )

        self.assertEqual(result, [{"country_id": (1, "Belgium")}])
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "read_group",
            [("is_company", "=", True)],
            ["country_id"],
            ["country_id"],
            lazy=False,
            offset=10,
            limit=5,
            orderby="country_id",
        )

    def test_read_group_serializes_nested_boolean_domain(self) -> None:
        self.executor.execute.return_value = [{"country_id": (1, "Belgium")}]

        result = self.model.read_group(
            [
                "|",
                ("active", "=", True),
                [
                    "&",
                    ("company_id", "=", 3),
                    ("name", "ilike", "Acme"),
                ],
            ],
            ["country_id"],
            ["country_id"],
        )

        self.assertEqual(result, [{"country_id": (1, "Belgium")}])
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "read_group",
            [
                "|",
                ("active", "=", True),
                "&",
                ("company_id", "=", 3),
                ("name", "ilike", "Acme"),
            ],
            ["country_id"],
            ["country_id"],
            lazy=True,
        )

    def test_read_group_validates_required_arguments(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one field"):
            self.model.read_group([], [], ["country_id"])

        with self.assertRaisesRegex(ValueError, "at least one groupby"):
            self.model.read_group([], ["country_id"], [])
