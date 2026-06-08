import unittest
from unittest.mock import Mock, call

from odoo_sdk.fields.commands import Command
from odoo_sdk.transport.errors import OdooAccessError
from odoo_sdk.fields.values import RelationValue
from odoo_sdk.env.env import OdooEnv
from odoo_sdk.records.model import OdooModel
from odoo_sdk.records.recordset import OdooRecordset


class TestOdooModel(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = Mock()
        self.model = OdooModel(self.executor, "res.partner")

    def test_search_returns_recordset(self) -> None:
        self.executor.execute.return_value = [1, 2]

        result = self.model.search([("active", "=", True)])

        self.assertIsInstance(result, OdooRecordset)
        self.assertEqual(result.ids, (1, 2))
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "search",
            [("active", "=", True)],
        )

    def test_search_supports_native_odoo_kwargs(self) -> None:
        self.executor.execute.return_value = [1, 2]

        result = self.model.search(
            [("active", "=", True)],
            limit=2,
            offset=5,
            order="name asc",
            context={"lang": "fr_FR"},
        )

        self.assertEqual(result.ids, (1, 2))
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "search",
            [("active", "=", True)],
            limit=2,
            offset=5,
            order="name asc",
            context={"lang": "fr_FR"},
        )

    def test_read_accepts_single_id(self) -> None:
        self.executor.execute.return_value = [{"id": 7, "name": "Acme"}]

        result = self.model.read(7, ["name"])

        self.assertEqual(result, [{"id": 7, "name": "Acme"}])
        self.executor.execute.assert_called_once_with(
            "res.partner", "read", [7], fields=["name"]
        )

    def test_read_propagates_sdk_error_without_wrapping(self) -> None:
        error = OdooAccessError(
            "Odoo access denied (res.partner.read)",
            operation="res.partner.read",
            model="res.partner",
            method="read",
        )
        self.executor.execute.side_effect = error

        with self.assertRaises(OdooAccessError) as caught:
            self.model.read(7, ["name"])

        self.assertIs(caught.exception, error)

    def test_read_delegates_to_recordset_read(self) -> None:
        recordset = Mock()
        recordset.ids = (3,)
        recordset.read.return_value = [{"id": 3, "name": "Demo"}]
        self.model._recordset = Mock(return_value=recordset)

        result = self.model.read(3, ["name"])

        self.assertEqual(result, [{"id": 3, "name": "Demo"}])
        self.model._recordset.assert_called_once_with(3)
        recordset.read.assert_called_once_with(["name"])
        self.executor.execute.assert_not_called()

    def test_create_delegates_to_executor(self) -> None:
        self.executor.execute.return_value = 101

        result = self.model.create({"name": "New"})

        self.assertEqual(result, 101)
        self.executor.execute.assert_called_once_with(
            "res.partner", "create", {"name": "New"}
        )

    def test_read_adapted_delegates_to_recordset_read_adapted(self) -> None:
        recordset = Mock()
        recordset.ids = (3,)
        recordset.read_adapted.return_value = [
            {"id": 3, "parent_id": RelationValue("res.partner", 7, "Parent")}
        ]
        self.model._recordset = Mock(return_value=recordset)

        result = self.model.read_adapted(3, ["parent_id"])

        self.assertEqual(
            result,
            [{"id": 3, "parent_id": RelationValue("res.partner", 7, "Parent")}],
        )
        self.model._recordset.assert_called_once_with(3)
        recordset.read_adapted.assert_called_once_with(["parent_id"])
        self.executor.execute.assert_not_called()

    def test_write_normalizes_single_id(self) -> None:
        self.executor.execute.return_value = True

        result = self.model.write(9, {"name": "Updated"})

        self.assertTrue(result)
        self.executor.execute.assert_called_once_with(
            "res.partner", "write", [9], {"name": "Updated"}
        )

    def test_write_delegates_to_recordset_write(self) -> None:
        recordset = Mock()
        recordset.ids = (9,)
        recordset.write.return_value = True
        self.model._recordset = Mock(return_value=recordset)

        result = self.model.write(9, {"name": "Updated"})

        self.assertTrue(result)
        self.model._recordset.assert_called_once_with(9)
        recordset.write.assert_called_once_with({"name": "Updated"})
        self.executor.execute.assert_not_called()

    def test_write_serializes_x2many_helpers_via_recordset(self) -> None:
        self.executor.execute.side_effect = [
            {"tag_ids": {"type": "many2many"}},
            True,
        ]

        result = self.model.write(9, {"tag_ids": Command.link(4)})

        self.assertTrue(result)
        self.assertEqual(
            self.executor.execute.call_args_list,
            [
                call(
                    "res.partner",
                    "fields_get",
                    allfields=["tag_ids"],
                    attributes=["type"],
                ),
                call(
                    "res.partner",
                    "write",
                    [9],
                    {"tag_ids": [(4, 4, 0)]},
                ),
            ],
        )

    def test_write_passes_empty_ids_and_values_to_executor(self) -> None:
        self.executor.execute.return_value = True

        result = self.model.write([], {})

        self.assertTrue(result)
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "write",
            [],
            {},
        )

    def test_unlink_normalizes_single_id(self) -> None:
        self.executor.execute.return_value = True

        result = self.model.unlink(10)

        self.assertTrue(result)
        self.executor.execute.assert_called_once_with("res.partner", "unlink", [10])

    def test_unlink_delegates_to_recordset_unlink(self) -> None:
        recordset = Mock()
        recordset.ids = (10,)
        recordset.unlink.return_value = True
        self.model._recordset = Mock(return_value=recordset)

        result = self.model.unlink(10)

        self.assertTrue(result)
        self.model._recordset.assert_called_once_with(10)
        recordset.unlink.assert_called_once_with()
        self.executor.execute.assert_not_called()

    def test_unlink_passes_empty_ids_to_executor(self) -> None:
        self.executor.execute.return_value = True

        result = self.model.unlink([])

        self.assertTrue(result)
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "unlink",
            [],
        )

    def test_fields_get_uses_keywords(self) -> None:
        self.executor.execute.return_value = {"name": {"type": "char"}}

        result = self.model.fields_get(["name"], ["type"])

        self.assertEqual(result, {"name": {"type": "char"}})
        self.executor.execute.assert_called_once_with(
            "res.partner", "fields_get", allfields=["name"], attributes=["type"]
        )

    def test_fields_get_reuses_cached_metadata(self) -> None:
        self.executor.execute.return_value = {"name": {"type": "char"}}

        first = self.model.fields_get(["name"], ["type"])
        second = self.model.fields_get(["name"], ["type"])

        self.assertEqual(first, second)
        self.assertIsNot(first, second)
        self.executor.execute.assert_called_once_with(
            "res.partner", "fields_get", allfields=["name"], attributes=["type"]
        )

    def test_browse_returns_recordset_without_io(self) -> None:
        result = self.model.browse(3)

        self.assertIsInstance(result, OdooRecordset)
        self.assertEqual(result.ids, (3,))
        self.executor.execute.assert_not_called()

    def test_browse_delegates_to_recordset_binding(self) -> None:
        recordset = Mock()
        recordset.ids = (3,)
        self.model._recordset = Mock(return_value=recordset)

        result = self.model.browse(3)

        self.assertIs(result, recordset)
        self.model._recordset.assert_called_once_with(3)
        self.executor.execute.assert_not_called()

    def test_browse_adapted_is_compatibility_alias_for_browse(self) -> None:
        recordset = Mock(spec=OdooRecordset)
        self.model._recordset = Mock(return_value=recordset)

        result = self.model.browse_adapted(3)

        self.assertIs(result, recordset)
        self.model._recordset.assert_called_once_with(3)
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

    def test_search_ids_delegates_to_recordset_search(self) -> None:
        recordset = Mock(spec=OdooRecordset)
        recordset.ids = (1, 2)
        self.model._search_recordset = Mock(return_value=recordset)

        result = self.model.search_ids(
            [("is_company", "=", True)],
            limit=2,
            offset=5,
            order="name",
            context={"lang": "en_US"},
        )

        self.assertEqual(result, [1, 2])
        self.model._search_recordset.assert_called_once_with(
            [("is_company", "=", True)],
            limit=2,
            offset=5,
            order="name",
            context={"lang": "en_US"},
        )
        self.executor.execute.assert_not_called()

    def test_env_bound_model_read_uses_env_context(self) -> None:
        self.executor.execute.return_value = [{"id": 3, "name": "Demo"}]
        model = OdooEnv(self.executor, {"lang": "en_US"})["res.partner"]

        result = model.browse(3).read(["name"])

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

        result = model.search_read([("is_company", "=", True)], fields=["name"])

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

    def test_search_read_delegates_to_recordset_search_read(self) -> None:
        recordset = Mock(spec=OdooRecordset)
        recordset.search_read.return_value = [{"id": 1, "name": "Acme"}]
        self.model._env.recordset = Mock(return_value=recordset)

        result = self.model.search_read(
            [("is_company", "=", True)],
            ["name"],
            limit=1,
            offset=0,
            order="name",
        )

        self.assertEqual(result, [{"id": 1, "name": "Acme"}])
        self.model._env.recordset.assert_called_once_with("res.partner")
        recordset.search_read.assert_called_once_with(
            [("is_company", "=", True)],
            fields=["name"],
            limit=1,
            offset=0,
            order="name",
        )
        self.executor.execute.assert_not_called()

    def test_search_read_adapted_delegates_to_recordset_search_read_adapted(self) -> None:
        recordset = Mock(spec=OdooRecordset)
        recordset.search_read_adapted.return_value = [
            {"id": 1, "parent_id": RelationValue("res.partner", 7, "Parent")}
        ]
        self.model._env.recordset = Mock(return_value=recordset)

        result = self.model.search_read_adapted(
            [("is_company", "=", True)],
            ["parent_id"],
            limit=1,
            offset=0,
            order="name",
        )

        self.assertEqual(
            result,
            [{"id": 1, "parent_id": RelationValue("res.partner", 7, "Parent")}],
        )
        self.model._env.recordset.assert_called_once_with("res.partner")
        recordset.search_read_adapted.assert_called_once_with(
            [("is_company", "=", True)],
            fields=["parent_id"],
            limit=1,
            offset=0,
            order="name",
        )
        self.executor.execute.assert_not_called()

    def test_search_read_raw_and_adapted_behaviors_remain_explicit(self) -> None:
        self.executor.execute.side_effect = [
            [{"id": 1, "parent_id": [7, "Parent"]}],
            [{"id": 1, "parent_id": [7, "Parent"]}],
            {"parent_id": {"type": "many2one", "relation": "res.partner"}},
        ]

        raw = self.model.search_read([("id", "=", 1)], ["parent_id"])
        adapted = self.model.search_read_adapted([("id", "=", 1)], ["parent_id"])

        self.assertEqual(raw, [{"id": 1, "parent_id": [7, "Parent"]}])
        self.assertEqual(
            adapted,
            [{"id": 1, "parent_id": RelationValue("res.partner", 7, "Parent")}],
        )

    def test_search_count_executes(self) -> None:
        self.executor.execute.return_value = 19

        result = self.model.search_count([("is_company", "=", True)])

        self.assertEqual(result, 19)
        self.executor.execute.assert_called_once_with(
            "res.partner", "search_count", [("is_company", "=", True)]
        )

    def test_search_count_delegates_to_recordset_search_count(self) -> None:
        recordset = Mock(spec=OdooRecordset)
        recordset.search_count.return_value = 19
        self.model._env.recordset = Mock(return_value=recordset)

        result = self.model.search_count([("is_company", "=", True)])

        self.assertEqual(result, 19)
        self.model._env.recordset.assert_called_once_with("res.partner")
        recordset.search_count.assert_called_once_with([("is_company", "=", True)])
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

    def test_name_get_passes_empty_ids_to_executor(self) -> None:
        self.executor.execute.return_value = []

        result = self.model.name_get([])

        self.assertEqual(result, [])
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "name_get",
            [],
        )

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

    def test_read_group_passes_empty_fields_and_groupby_to_executor(self) -> None:
        self.executor.execute.return_value = []

        result = self.model.read_group([], [], [])

        self.assertEqual(result, [])
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "read_group",
            [],
            [],
            [],
            lazy=True,
        )
