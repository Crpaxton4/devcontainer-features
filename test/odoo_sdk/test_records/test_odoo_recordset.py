import unittest
from datetime import date, datetime, timezone
from unittest.mock import Mock, call

from odoo_sdk.transport.errors import OdooMissingRecordError
from odoo_sdk.fields.values import RelationCollection, RelationValue
from odoo_sdk.env.env import OdooEnv
from odoo_sdk.transport.executor import OdooExecutor
from odoo_sdk.records.recordset import OdooRecordset
from odoo_sdk.fields.commands import Command

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

    def test_len_and_bool_follow_bound_identity(self) -> None:
        empty = OdooRecordset(self.env, "res.partner", [])
        populated = OdooRecordset(self.env, "res.partner", [3, 1, 2])

        self.assertEqual(len(empty), 0)
        self.assertFalse(empty)
        self.assertEqual(len(populated), 3)
        self.assertTrue(populated)

    def test_ensure_one_returns_same_singleton_recordset(self) -> None:
        recordset = OdooRecordset(self.env, "res.partner", [7])

        result = recordset.ensure_one()

        self.assertIs(result, recordset)

    def test_ensure_one_raises_on_empty_or_multi_recordsets(self) -> None:
        with self.assertRaisesRegex(ValueError, "Expected singleton"):
            OdooRecordset(self.env, "res.partner", []).ensure_one()

        with self.assertRaisesRegex(ValueError, "Expected singleton"):
            OdooRecordset(self.env, "res.partner", [7, 8]).ensure_one()

    def test_id_returns_singleton_identifier(self) -> None:
        recordset = OdooRecordset(self.env, "res.partner", [7])

        self.assertEqual(recordset.id, 7)

    def test_id_raises_when_recordset_is_not_singleton(self) -> None:
        with self.assertRaisesRegex(ValueError, "Expected singleton"):
            _ = OdooRecordset(self.env, "res.partner", [7, 8]).id

    def test_iteration_yields_singleton_recordsets_in_order(self) -> None:
        recordset = OdooRecordset(self.env, "res.partner", [3, 1, 2])

        result = list(recordset)

        self.assertEqual([item.ids for item in result], [(3,), (1,), (2,)])
        self.assertTrue(all(isinstance(item, OdooRecordset) for item in result))
        self.assertTrue(all(item.env is self.env for item in result))
        self.assertTrue(all(item.model_name == "res.partner" for item in result))

    def test_integer_index_returns_singleton_recordset(self) -> None:
        recordset = OdooRecordset(self.env, "res.partner", [3, 1, 2])

        result = recordset[1]

        self.assertIsInstance(result, OdooRecordset)
        self.assertEqual(result.ids, (1,))
        self.assertIs(result.env, self.env)

    def test_slice_returns_same_model_recordset_subset(self) -> None:
        recordset = OdooRecordset(self.env, "res.partner", [3, 1, 2, 9])

        result = recordset[1:3]

        self.assertIsInstance(result, OdooRecordset)
        self.assertEqual(result.ids, (1, 2))
        self.assertIs(result.env, self.env)
        self.assertEqual(result.model_name, "res.partner")

    def test_integer_index_raises_index_error_when_out_of_range(self) -> None:
        recordset = OdooRecordset(self.env, "res.partner", [])

        with self.assertRaises(IndexError):
            _ = recordset[0]

    def test_scalar_field_access_requires_singleton(self) -> None:
        with self.assertRaisesRegex(ValueError, "Expected singleton"):
            _ = OdooRecordset(self.env, "res.partner", [7, 8]).name

    def test_scalar_field_access_prefetches_across_iteration_and_caches(self) -> None:
        self.executor.execute.side_effect = [
            {"name": {"type": "char"}},
            [{"id": 7, "name": "Acme"}, {"id": 8, "name": "Beta"}],
        ]
        first, second = list(OdooRecordset(self.env, "res.partner", [7, 8]))

        self.assertEqual(first.name, "Acme")
        self.assertEqual(second.name, "Beta")
        self.assertEqual(
            self.executor.execute.call_args_list,
            [
                call(
                    "res.partner",
                    "fields_get",
                    allfields=["name"],
                    attributes=["type", "relation"],
                    context={"lang": "en_US"},
                ),
                call(
                    "res.partner",
                    "read",
                    [7, 8],
                    context={"lang": "en_US"},
                    fields=["name"],
                ),
            ],
        )

    def test_many2one_field_access_returns_related_recordset(self) -> None:
        self.executor.execute.side_effect = [
            {"parent_id": {"type": "many2one", "relation": "res.partner"}},
            [{"id": 7, "parent_id": [3, "Parent"]}],
            {"name": {"type": "char"}},
            [{"id": 3, "name": "Parent"}],
        ]
        recordset = OdooRecordset(self.env, "res.partner", [7])

        parent = recordset.parent_id

        self.assertIsInstance(parent, OdooRecordset)
        self.assertEqual(parent.model_name, "res.partner")
        self.assertEqual(parent.ids, (3,))
        self.assertEqual(parent.name, "Parent")

    def test_unknown_attribute_raises_attribute_error(self) -> None:
        self.executor.execute.return_value = {}
        recordset = OdooRecordset(self.env, "res.partner", [7])

        with self.assertRaises(AttributeError):
            _ = recordset.not_a_real_field

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

    def test_read_propagates_sdk_error_without_wrapping(self) -> None:
        error = OdooMissingRecordError(
            "Odoo record was not found (res.partner.read)",
            operation="res.partner.read",
            model="res.partner",
            method="read",
        )
        self.executor.execute.side_effect = error
        recordset = OdooRecordset(self.env, "res.partner", [7])

        with self.assertRaises(OdooMissingRecordError) as caught:
            recordset.read(["name"])

        self.assertIs(caught.exception, error)

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

    def test_search_read_materializes_raw_rows_with_context(self) -> None:
        self.executor.execute.return_value = [{"id": 7, "name": "Acme"}]
        recordset = OdooRecordset(self.env, "res.partner")

        result = recordset.search_read(
            [("active", "=", True)],
            fields=["name"],
            limit=1,
            order="name asc",
        )

        self.assertEqual(result, [{"id": 7, "name": "Acme"}])
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "search_read",
            [("active", "=", True)],
            context={"lang": "en_US"},
            limit=1,
            order="name asc",
            fields=["name"],
        )

    def test_search_ids_returns_list_from_search_recordset(self) -> None:
        self.executor.execute.return_value = [7, 8]
        recordset = OdooRecordset(self.env, "res.partner")

        result = recordset.search_ids(
            [("active", "=", True)],
            limit=2,
            offset=1,
            order="name asc",
        )

        self.assertEqual(result, [7, 8])
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "search",
            [("active", "=", True)],
            context={"lang": "en_US"},
            limit=2,
            offset=1,
            order="name asc",
        )

    def test_search_count_executes_with_context(self) -> None:
        self.executor.execute.return_value = 9
        recordset = OdooRecordset(self.env, "res.partner")

        result = recordset.search_count([("active", "=", True)])

        self.assertEqual(result, 9)
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "search_count",
            [("active", "=", True)],
            context={"lang": "en_US"},
        )

    def test_search_write_delegates_to_search_then_write(self) -> None:
        self.executor.execute.side_effect = [[], True]
        recordset = OdooRecordset(self.env, "res.partner")

        result = recordset.search_write(
            [("active", "=", True)],
            {},
        )

        self.assertTrue(result)
        self.assertEqual(
            self.executor.execute.call_args_list,
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

    def test_search_unlink_delegates_to_search_then_unlink(self) -> None:
        self.executor.execute.side_effect = [[], True]
        recordset = OdooRecordset(self.env, "res.partner")

        result = recordset.search_unlink(
            [("active", "=", True)],
        )

        self.assertTrue(result)
        self.assertEqual(
            self.executor.execute.call_args_list,
            [
                call(
                    "res.partner",
                    "search",
                    [("active", "=", True)],
                    context={"lang": "en_US"},
                ),
                call(
                    "res.partner",
                    "unlink",
                    [],
                    context={"lang": "en_US"},
                ),
            ],
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
                    Command.link(3),
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
                "tag_ids": Command.set([5, 3]),
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

    def test_write_passes_x2many_helper_on_scalar_field_to_executor(self) -> None:
        self.executor.execute.side_effect = [
            {"name": {"type": "char"}},
            True,
        ]
        recordset = OdooRecordset(self.env, "res.partner", [7])

        result = recordset.write({"name": Command.link(3)})

        self.assertTrue(result)
        self.assertEqual(
            self.executor.execute.call_args_list,
            [
                call(
                    "res.partner",
                    "fields_get",
                    allfields=["name"],
                    attributes=["type"],
                    context={"lang": "en_US"},
                ),
                call(
                    "res.partner",
                    "write",
                    [7],
                    {"name": Command.link(3)},
                    context={"lang": "en_US"},
                ),
            ],
        )

    def test_write_passes_empty_ids_to_executor(self) -> None:
        self.executor.execute.return_value = True

        result = OdooRecordset(self.env, "res.partner", []).write({"name": "Acme"})

        self.assertTrue(result)
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "write",
            [],
            {"name": "Acme"},
            context={"lang": "en_US"},
        )

    def test_write_passes_empty_values_to_executor(self) -> None:
        self.executor.execute.return_value = True

        result = OdooRecordset(self.env, "res.partner", [1]).write({})

        self.assertTrue(result)
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "write",
            [1],
            {},
            context={"lang": "en_US"},
        )

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

    def test_unlink_passes_empty_ids_to_executor(self) -> None:
        self.executor.execute.return_value = True

        result = OdooRecordset(self.env, "res.partner", []).unlink()

        self.assertTrue(result)
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "unlink",
            [],
            context={"lang": "en_US"},
        )

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
