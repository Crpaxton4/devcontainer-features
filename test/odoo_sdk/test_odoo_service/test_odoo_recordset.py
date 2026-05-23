import unittest
from datetime import date, datetime, timezone
from unittest.mock import Mock, call

from odoo_sdk.odoo_service.field_values import RelationCollection, RelationValue
from odoo_sdk.odoo_service.odoo_env import OdooEnv
from odoo_sdk.odoo_service.odoo_executor import OdooExecutor
from odoo_sdk.odoo_service.odoo_recordset import OdooRecordset
from odoo_sdk.odoo_service.x2many_commands import X2ManyCommand


class TestOdooRecordset(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = Mock(spec=OdooExecutor)
        self.env = OdooEnv(self.executor, {"lang": "en_US"})

    def test_single_integer_id_is_normalized_to_singleton_tuple(self) -> None:
        recordset = OdooRecordset(self.env, "res.partner", 7)

        self.assertEqual(recordset.ids, (7,))

    def test_identity_preserves_model_env_and_ordered_ids(self) -> None:
        input_ids = [3, 1, 2]

        recordset = OdooRecordset(self.env, "res.partner", input_ids)
        input_ids.append(4)

        self.assertIs(recordset.env, self.env)
        self.assertEqual(recordset.model_name, "res.partner")
        self.assertEqual(recordset.ids, (3, 1, 2))

    def test_read_returns_empty_rows_without_io_for_empty_ids(self) -> None:
        recordset = OdooRecordset(self.env, "res.partner", [])

        result = recordset.read(["name"])

        self.assertEqual(result, [])
        self.executor.execute.assert_not_called()

    def test_read_materializes_raw_rows_with_context(self) -> None:
        self.executor.execute.return_value = [{"id": 7, "name": "Acme"}]
        recordset = OdooRecordset(self.env, "res.partner", [7])

        result = recordset.read(["name"])

        self.assertEqual(result, [{"id": 7, "name": "Acme"}])
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "read",
            [7],
            context={"lang": "en_US"},
            fields=["name"],
        )

    def test_read_adapted_materializes_relation_date_and_binary_values(self) -> None:
        self.executor.execute.side_effect = [
            [
                {
                    "id": 7,
                    "parent_id": [3, "Parent"],
                    "category_id": [9, 5],
                    "birthday": "2026-05-23",
                    "write_date": "2026-05-23 10:15:00",
                    "image_128": "aGVsbG8=",
                }
            ],
            {
                "parent_id": {"type": "many2one", "relation": "res.partner"},
                "category_id": {
                    "type": "many2many",
                    "relation": "res.partner.category",
                },
                "birthday": {"type": "date"},
                "write_date": {"type": "datetime"},
                "image_128": {"type": "binary"},
            },
        ]
        recordset = OdooRecordset(self.env, "res.partner", [7])

        result = recordset.read_adapted(
            ["parent_id", "category_id", "birthday", "write_date", "image_128"]
        )

        self.assertEqual(
            result,
            [
                {
                    "id": 7,
                    "parent_id": RelationValue("res.partner", 3, "Parent"),
                    "category_id": RelationCollection(
                        "res.partner.category", (9, 5)
                    ),
                    "birthday": date(2026, 5, 23),
                    "write_date": datetime(
                        2026, 5, 23, 10, 15, 0, tzinfo=timezone.utc
                    ),
                    "image_128": b"hello",
                }
            ],
        )
        self.assertEqual(self.executor.execute.call_count, 2)
        self.assertEqual(
            self.executor.execute.call_args_list[1].kwargs,
            {
                "allfields": [
                    "parent_id",
                    "category_id",
                    "birthday",
                    "write_date",
                    "image_128",
                ],
                "attributes": ["type", "relation"],
                "context": {"lang": "en_US"},
            },
        )

    def test_read_adapted_reuses_cached_metadata_on_subsequent_reads(self) -> None:
        self.executor.execute.side_effect = [
            [{"id": 7, "parent_id": [3, "Parent"]}],
            {"parent_id": {"type": "many2one", "relation": "res.partner"}},
            [{"id": 7, "parent_id": [4, "Updated"]}],
        ]
        recordset = OdooRecordset(self.env, "res.partner", [7])

        first = recordset.read_adapted(["parent_id"])
        second = recordset.read_adapted(["parent_id"])

        self.assertEqual(
            first,
            [{"id": 7, "parent_id": RelationValue("res.partner", 3, "Parent")}],
        )
        self.assertEqual(
            second,
            [{"id": 7, "parent_id": RelationValue("res.partner", 4, "Updated")}],
        )
        self.assertEqual(self.executor.execute.call_count, 3)

    def test_read_adapted_with_only_id_field_skips_metadata_lookup(self) -> None:
        self.executor.execute.return_value = [{"id": 7}]
        recordset = OdooRecordset(self.env, "res.partner", [7])

        result = recordset.read_adapted(["id"])

        self.assertEqual(result, [{"id": 7}])
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "read",
            [7],
            context={"lang": "en_US"},
            fields=["id"],
        )

    def test_read_adapted_without_requested_fields_uses_returned_record_keys(self) -> None:
        self.executor.execute.side_effect = [
            [{"id": 7, "parent_id": [3, "Parent"], "birthday": "2026-05-23"}],
            {
                "parent_id": {"type": "many2one", "relation": "res.partner"},
                "birthday": {"type": "date"},
            },
        ]
        recordset = OdooRecordset(self.env, "res.partner", [7])

        result = recordset.read_adapted()

        self.assertEqual(
            result,
            [
                {
                    "id": 7,
                    "parent_id": RelationValue("res.partner", 3, "Parent"),
                    "birthday": date(2026, 5, 23),
                }
            ],
        )
        self.assertEqual(
            self.executor.execute.call_args_list[1],
            unittest.mock.call(
                "res.partner",
                "fields_get",
                allfields=["parent_id", "birthday"],
                attributes=["type", "relation"],
                context={"lang": "en_US"},
            ),
        )

    def test_search_read_adapted_shares_recordset_adaptation_path(self) -> None:
        self.executor.execute.side_effect = [
            [{"id": 7, "parent_id": [3, "Parent"]}],
            {"parent_id": {"type": "many2one", "relation": "res.partner"}},
        ]
        recordset = OdooRecordset(self.env, "res.partner")

        result = recordset.search_read_adapted(
            [("active", "=", True)],
            fields=["parent_id"],
            limit=1,
            order="name asc",
        )

        self.assertEqual(
            result,
            [{"id": 7, "parent_id": RelationValue("res.partner", 3, "Parent")}],
        )
        self.assertEqual(
            self.executor.execute.call_args_list[0],
            unittest.mock.call(
                "res.partner",
                "search_read",
                [("active", "=", True)],
                context={"lang": "en_US"},
                limit=1,
                order="name asc",
                fields=["parent_id"],
            ),
        )

    def test_write_updates_current_ids_with_context(self) -> None:
        self.executor.execute.return_value = True
        recordset = OdooRecordset(self.env, "res.partner", [7, 8])

        result = recordset.write({"comment": "Updated"})

        self.assertTrue(result)
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "write",
            [7, 8],
            {"comment": "Updated"},
            context={"lang": "en_US"},
        )

    def test_write_serializes_x2many_helpers_with_shared_ordering(self) -> None:
        self.executor.execute.side_effect = [
            {"tag_ids": {"type": "many2many"}},
            True,
        ]
        recordset = OdooRecordset(self.env, "res.partner", [7, 8])

        result = recordset.write(
            {
                "tag_ids": [
                    X2ManyCommand.link(3),
                    (4, 5),
                ]
            }
        )

        self.assertTrue(result)
        self.assertEqual(
            self.executor.execute.call_args_list,
            [
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
                    [7, 8],
                    {"tag_ids": [(4, 3, 0), (4, 5, 0)]},
                    context={"lang": "en_US"},
                ),
            ],
        )

    def test_write_serializes_single_x2many_helper_and_preserves_scalars(self) -> None:
        self.executor.execute.side_effect = [
            {"tag_ids": {"type": "many2many"}},
            True,
        ]
        recordset = OdooRecordset(self.env, "res.partner", [7])

        result = recordset.write(
            {
                "name": "Updated",
                "tag_ids": X2ManyCommand.set([5, 3]),
            }
        )

        self.assertTrue(result)
        self.assertEqual(
            self.executor.execute.call_args_list,
            [
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
                    [7],
                    {
                        "name": "Updated",
                        "tag_ids": [(6, 0, [5, 3])],
                    },
                    context={"lang": "en_US"},
                ),
            ],
        )

    def test_write_rejects_invalid_x2many_raw_tuple_before_write(self) -> None:
        self.executor.execute.return_value = {"tag_ids": {"type": "many2many"}}
        recordset = OdooRecordset(self.env, "res.partner", [7])

        with self.assertRaisesRegex(ValueError, "positive integer id"):
            recordset.write({"tag_ids": [(4, 0)]})

        self.executor.execute.assert_called_once_with(
            "res.partner",
            "fields_get",
            allfields=["tag_ids"],
            attributes=["type"],
            context={"lang": "en_US"},
        )

    def test_write_rejects_x2many_helper_on_scalar_field(self) -> None:
        self.executor.execute.return_value = {"name": {"type": "char"}}
        recordset = OdooRecordset(self.env, "res.partner", [7])

        with self.assertRaisesRegex(ValueError, "one2many or many2many"):
            recordset.write({"name": X2ManyCommand.link(3)})

        self.executor.execute.assert_called_once_with(
            "res.partner",
            "fields_get",
            allfields=["name"],
            attributes=["type"],
            context={"lang": "en_US"},
        )

    def test_write_rejects_empty_ids_and_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one id"):
            OdooRecordset(self.env, "res.partner", []).write({"name": "Acme"})

        with self.assertRaisesRegex(ValueError, "at least one value"):
            OdooRecordset(self.env, "res.partner", [1]).write({})

    def test_unlink_deletes_current_ids_with_context(self) -> None:
        self.executor.execute.return_value = True
        recordset = OdooRecordset(self.env, "res.partner", [7, 8])

        result = recordset.unlink()

        self.assertTrue(result)
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "unlink",
            [7, 8],
            context={"lang": "en_US"},
        )

    def test_unlink_rejects_empty_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one id"):
            OdooRecordset(self.env, "res.partner", []).unlink()

    def test_exists_preserves_surviving_id_order(self) -> None:
        self.executor.execute.return_value = [2, 1]
        recordset = OdooRecordset(self.env, "res.partner", [3, 1, 2])

        result = recordset.exists()

        self.assertIsInstance(result, OdooRecordset)
        self.assertEqual(result.ids, (1, 2))
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "search",
            [("id", "in", [3, 1, 2])],
            context={"lang": "en_US"},
        )

    def test_exists_returns_empty_recordset_without_io_for_empty_ids(self) -> None:
        recordset = OdooRecordset(self.env, "res.partner", [])

        result = recordset.exists()

        self.assertEqual(result.ids, ())
        self.executor.execute.assert_not_called()

    def test_browse_returns_same_model_recordset_without_io(self) -> None:
        recordset = OdooRecordset(self.env, "res.partner", [7])

        result = recordset.browse([9, 10])

        self.assertIsInstance(result, OdooRecordset)
        self.assertEqual(result.ids, (9, 10))
        self.assertEqual(result.model_name, "res.partner")
        self.assertIs(result.env, self.env)
        self.executor.execute.assert_not_called()

    def test_search_returns_new_recordset_with_domain_options_and_context(self) -> None:
        self.executor.execute.return_value = [4, 5]
        recordset = OdooRecordset(self.env, "res.partner", [99])

        result = recordset.search(
            [("active", "=", True)],
            limit=2,
            offset=1,
            order="name asc",
        )

        self.assertIsInstance(result, OdooRecordset)
        self.assertEqual(result.ids, (4, 5))
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "search",
            [("active", "=", True)],
            context={"lang": "en_US"},
            limit=2,
            offset=1,
            order="name asc",
        )

    def test_with_context_returns_new_recordset_without_mutating_original(self) -> None:
        recordset = OdooRecordset(self.env, "res.partner", [7, 8])

        derived = recordset.with_context({"tz": "UTC"})

        self.assertIsNot(derived, recordset)
        self.assertEqual(recordset.env.context, {"lang": "en_US"})
        self.assertEqual(derived.env.context, {"lang": "en_US", "tz": "UTC"})
        self.assertEqual(derived.ids, (7, 8))
        self.assertEqual(derived.model_name, "res.partner")
        self.executor.execute.assert_not_called()