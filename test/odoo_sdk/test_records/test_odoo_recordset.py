import unittest
from datetime import date, datetime, timezone
from typing import Sequence
from unittest.mock import Mock, call

from odoo_sdk.fields.commands import Command
from odoo_sdk.fields.values import RelationCollection, RelationValue
from odoo_sdk.records.recordset import OdooRecordset
from odoo_sdk.transport.errors import OdooMissingRecordError
from odoo_sdk.transport.executor import OdooExecutor


class TestOdooRecordset(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = Mock(spec=OdooExecutor)

    def test_single_integer_id_is_normalized_to_singleton_tuple(self) -> None:
        recordset = OdooRecordset(
            self.executor, "res.partner", 7, context={"lang": "en_US"}
        )

        self.assertEqual(recordset.ids, (7,))

    def test_identity_preserves_model_env_and_ordered_ids(self) -> None:
        input_ids = [3, 1, 2]

        recordset = OdooRecordset(
            self.executor, "res.partner", input_ids, context={"lang": "en_US"}
        )
        input_ids.append(4)

        self.assertEqual(recordset.model_name, "res.partner")
        self.assertEqual(recordset.ids, (3, 1, 2))

    def test_len_and_bool_follow_bound_identity(self) -> None:
        empty = OdooRecordset(
            self.executor, "res.partner", [], context={"lang": "en_US"}
        )
        populated = OdooRecordset(
            self.executor, "res.partner", [3, 1, 2], context={"lang": "en_US"}
        )

        self.assertEqual(len(empty), 0)
        self.assertFalse(empty)
        self.assertEqual(len(populated), 3)
        self.assertTrue(populated)

    def test_ensure_one_returns_same_singleton_recordset(self) -> None:
        recordset = OdooRecordset(
            self.executor, "res.partner", [7], context={"lang": "en_US"}
        )

        result = recordset.ensure_one()

        self.assertIs(result, recordset)

    def test_ensure_one_raises_on_empty_or_multi_recordsets(self) -> None:
        with self.assertRaisesRegex(ValueError, "Expected singleton"):
            OdooRecordset(
                self.executor, "res.partner", [], context={"lang": "en_US"}
            ).ensure_one()

        with self.assertRaisesRegex(ValueError, "Expected singleton"):
            OdooRecordset(
                self.executor, "res.partner", [7, 8], context={"lang": "en_US"}
            ).ensure_one()

    def test_id_returns_singleton_identifier(self) -> None:
        recordset = OdooRecordset(
            self.executor, "res.partner", [7], context={"lang": "en_US"}
        )

        self.assertEqual(recordset.id, 7)

    def test_id_raises_when_recordset_is_not_singleton(self) -> None:
        with self.assertRaisesRegex(ValueError, "Expected singleton"):
            _ = OdooRecordset(
                self.executor, "res.partner", [7, 8], context={"lang": "en_US"}
            ).id

    def test_iteration_yields_singleton_recordsets_in_order(self) -> None:
        recordset = OdooRecordset(
            self.executor, "res.partner", [3, 1, 2], context={"lang": "en_US"}
        )

        result = list(recordset)

        self.assertEqual([item.ids for item in result], [(3,), (1,), (2,)])
        self.assertTrue(all(item.model_name == "res.partner" for item in result))

    # TODO Fix incorrect arg getitem handling
    # def test_integer_index_raises(self) -> None:
    #     recordset = OdooRecordset(
    #         self.executor, "res.partner", [3, 1, 2], context={"lang": "en_US"}
    #     )

    #     # with self.assertRaises(ValueError):
    #     _ = recordset[1]

    # def test_slice_index_raises(self) -> None:
    #     recordset = OdooRecordset(
    #         self.executor, "res.partner", [3, 1, 2, 9], context={"lang": "en_US"}
    #     )

    #     # with self.assertRaises(ValueError):
    #     _ = recordset[1:3]

    # def test_integer_index_raises_index_error_when_out_of_range(self) -> None:
    #     recordset = OdooRecordset(
    #         self.executor, "res.partner", [], context={"lang": "en_US"}
    #     )

    #      _ = recordset[0]

    def test_scalar_field_access_requires_singleton(self) -> None:
        with self.assertRaisesRegex(ValueError, "Expected singleton"):
            _ = OdooRecordset(
                self.executor, "res.partner", [7, 8], context={"lang": "en_US"}
            ).name

    def test_scalar_field_access_prefetches_across_iteration_and_caches(self) -> None:
        self.executor.execute.side_effect = [
            {"name": {"type": "char"}},
            [{"id": 7, "name": "Acme"}, {"id": 8, "name": "Beta"}],
        ]
        first, second = list(
            OdooRecordset(
                self.executor, "res.partner", [7, 8], context={"lang": "en_US"}
            )
        )

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
        recordset = OdooRecordset(
            self.executor, "res.partner", [7], context={"lang": "en_US"}
        )

        parent = recordset.parent_id

        self.assertIsInstance(parent, OdooRecordset)
        self.assertEqual(parent.model_name, "res.partner")
        self.assertEqual(parent.ids, (3,))
        self.assertEqual(parent.name, "Parent")

    def test_unknown_attribute_raises_attribute_error(self) -> None:
        self.executor.execute.return_value = {}
        recordset = OdooRecordset(
            self.executor, "res.partner", [7], context={"lang": "en_US"}
        )

        with self.assertRaises(AttributeError):
            _ = recordset.not_a_real_field

    def test_read_returns_empty_rows_without_io_for_empty_ids(self) -> None:
        recordset = OdooRecordset(
            self.executor, "res.partner", [], context={"lang": "en_US"}
        )

        result = recordset.read(["name"])

        self.assertEqual(result, [])
        self.executor.execute.assert_not_called()

    def test_read_materializes_raw_rows_with_context(self) -> None:
        self.executor.execute.return_value = [{"id": 7, "name": "Acme"}]
        recordset = OdooRecordset(
            self.executor, "res.partner", [7], context={"lang": "en_US"}
        )

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
        recordset = OdooRecordset(
            self.executor, "res.partner", [7], context={"lang": "en_US"}
        )

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
        recordset = OdooRecordset(
            self.executor, "res.partner", [7], context={"lang": "en_US"}
        )

        result = recordset.read_adapted(
            ["parent_id", "category_id", "birthday", "write_date", "image_128"]
        )

        self.assertEqual(
            result,
            [
                {
                    "id": 7,
                    "parent_id": RelationValue("res.partner", 3, "Parent"),
                    "category_id": RelationCollection("res.partner.category", (9, 5)),
                    "birthday": date(2026, 5, 23),
                    "write_date": datetime(2026, 5, 23, 10, 15, 0, tzinfo=timezone.utc),
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
        recordset = OdooRecordset(
            self.executor, "res.partner", [7], context={"lang": "en_US"}
        )

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
        recordset = OdooRecordset(
            self.executor, "res.partner", [7], context={"lang": "en_US"}
        )

        result = recordset.read_adapted(["id"])

        self.assertEqual(result, [{"id": 7}])
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "read",
            [7],
            context={"lang": "en_US"},
            fields=["id"],
        )

    def test_read_adapted_without_requested_fields_uses_returned_record_keys(
        self,
    ) -> None:
        self.executor.execute.side_effect = [
            [{"id": 7, "parent_id": [3, "Parent"], "birthday": "2026-05-23"}],
            {
                "parent_id": {"type": "many2one", "relation": "res.partner"},
                "birthday": {"type": "date"},
            },
        ]
        recordset = OdooRecordset(
            self.executor, "res.partner", [7], context={"lang": "en_US"}
        )

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
        recordset = OdooRecordset(
            self.executor, "res.partner", context={"lang": "en_US"}
        )

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
        recordset = OdooRecordset(
            self.executor, "res.partner", context={"lang": "en_US"}
        )

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
        recordset = OdooRecordset(
            self.executor, "res.partner", context={"lang": "en_US"}
        )

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
        recordset = OdooRecordset(
            self.executor, "res.partner", context={"lang": "en_US"}
        )

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
        recordset = OdooRecordset(
            self.executor, "res.partner", context={"lang": "en_US"}
        )

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
        recordset = OdooRecordset(
            self.executor, "res.partner", context={"lang": "en_US"}
        )

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
        recordset = OdooRecordset(
            self.executor, "res.partner", [7, 8], context={"lang": "en_US"}
        )

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
        recordset = OdooRecordset(
            self.executor, "res.partner", [7, 8], context={"lang": "en_US"}
        )

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
        recordset = OdooRecordset(
            self.executor, "res.partner", [7], context={"lang": "en_US"}
        )

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
        recordset = OdooRecordset(
            self.executor, "res.partner", [7], context={"lang": "en_US"}
        )

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
        recordset = OdooRecordset(
            self.executor, "res.partner", [7], context={"lang": "en_US"}
        )

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

        result = OdooRecordset(
            self.executor, "res.partner", [], context={"lang": "en_US"}
        ).write({"name": "Acme"})

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

        result = OdooRecordset(
            self.executor, "res.partner", [1], context={"lang": "en_US"}
        ).write({})

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
        recordset = OdooRecordset(
            self.executor, "res.partner", [7, 8], context={"lang": "en_US"}
        )

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

        result = OdooRecordset(
            self.executor, "res.partner", [], context={"lang": "en_US"}
        ).unlink()

        self.assertTrue(result)
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "unlink",
            [],
            context={"lang": "en_US"},
        )

    def test_exists_preserves_surviving_id_order(self) -> None:
        self.executor.execute.return_value = [2, 1]
        recordset = OdooRecordset(
            self.executor, "res.partner", [3, 1, 2], context={"lang": "en_US"}
        )

        result = recordset.exists()

        self.assertIsInstance(result, OdooRecordset)
        self.assertEqual(result.ids, (1, 2))
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "search",
            [("id", "in", (3, 1, 2))],
            context={"lang": "en_US"},
        )

    def test_exists_returns_empty_recordset_without_io_for_empty_ids(self) -> None:
        recordset = OdooRecordset(
            self.executor, "res.partner", [], context={"lang": "en_US"}
        )

        result = recordset.exists()

        self.assertEqual(result.ids, ())
        self.executor.execute.assert_not_called()

    def test_browse_returns_same_model_recordset_without_io(self) -> None:
        recordset = OdooRecordset(
            self.executor, "res.partner", [7], context={"lang": "en_US"}
        )

        result = recordset.browse([9, 10])

        self.assertIsInstance(result, OdooRecordset)
        self.assertEqual(result.ids, (9, 10))
        self.assertEqual(result.model_name, "res.partner")
        self.executor.execute.assert_not_called()

    def test_search_returns_new_recordset_with_domain_options_and_context(self) -> None:
        self.executor.execute.return_value = [4, 5]
        recordset = OdooRecordset(
            self.executor, "res.partner", [99], context={"lang": "en_US"}
        )

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
        recordset = OdooRecordset(
            self.executor, "res.partner", [7, 8], context={"lang": "en_US"}
        )

        derived = recordset.with_context({"tz": "UTC"})

        self.assertIsNot(derived, recordset)
        self.assertEqual(recordset.context, {"lang": "en_US"})
        self.assertEqual(derived.context, {"lang": "en_US", "tz": "UTC"})
        self.assertEqual(derived.ids, (7, 8))
        self.assertEqual(derived.model_name, "res.partner")
        self.executor.execute.assert_not_called()

    def test_read_group_groupby_only_returns_one_tuple_per_group(self) -> None:
        recordset = OdooRecordset(
            self.executor, "sale.order", [], context={"lang": "en_US"}
        )
        self.executor.execute.return_value = [
            {"stage_id": 1},
            {"stage_id": 2},
        ]

        result = recordset._read_group(groupby=("stage_id",))

        self.assertEqual(result, [(1,), (2,)])
        self.executor.execute.assert_called_once_with(
            "sale.order",
            "read_group",
            [],  # domain
            [],  # server_aggregates (fields)
            ["stage_id"],  # groupby
            context={"lang": "en_US"},
            lazy=False,
        )

    def test_read_group_aggregate_only_returns_aggregate_values(self) -> None:
        recordset = OdooRecordset(
            self.executor, "sale.order", [], context={"lang": "en_US"}
        )
        # read_group response uses base field names, not 'field:agg' spec strings
        self.executor.execute.return_value = [{"amount_total": 500.0}]

        result = recordset._read_group(aggregates=("amount_total:sum",))

        self.assertEqual(result, [(500.0,)])

    def test_read_group_groupby_and_aggregates_returns_combined_tuples(self) -> None:
        recordset = OdooRecordset(
            self.executor, "sale.order", [], context={"lang": "en_US"}
        )
        # read_group response uses base field names
        self.executor.execute.return_value = [
            {"stage_id": 1, "amount_total": 300.0},
            {"stage_id": 2, "amount_total": 700.0},
        ]

        result = recordset._read_group(
            groupby=("stage_id",), aggregates=("amount_total:sum",)
        )

        self.assertEqual(result, [(1, 300.0), (2, 700.0)])

    def test_read_group_having_raises_not_implemented_error(self) -> None:
        recordset = OdooRecordset(
            self.executor, "sale.order", [], context={"lang": "en_US"}
        )

        with self.assertRaises(NotImplementedError):
            recordset._read_group(
                aggregates=("amount_total:sum",),
                having=[("amount_total:sum", ">=", 100)],
            )

    def test_read_group_none_domain_passes_empty_list_to_server(self) -> None:
        recordset = OdooRecordset(
            self.executor, "sale.order", [], context={"lang": "en_US"}
        )
        self.executor.execute.return_value = []

        recordset._read_group(domain=None, groupby=("stage_id",))

        _call_args = self.executor.execute.call_args.args
        self.assertEqual(_call_args[2], [])

    def test_read_group_returns_empty_list_when_server_returns_no_rows(self) -> None:
        recordset = OdooRecordset(
            self.executor, "sale.order", [], context={"lang": "en_US"}
        )
        self.executor.execute.return_value = []

        result = recordset._read_group(
            domain=[("active", "=", True)], groupby=("stage_id",)
        )

        self.assertEqual(result, [])

    def test_read_group_recordset_aggregate_converts_ids_to_odoo_recordset(
        self,
    ) -> None:
        recordset = OdooRecordset(
            self.executor, "sale.order", [], context={"lang": "en_US"}
        )
        # read_group response uses base field names (partner_id, not partner_id:recordset)
        self.executor.execute.side_effect = [
            [{"stage_id": 1, "partner_id": [7, 8]}],
            {"partner_id": {"type": "many2one", "relation": "res.partner"}},
        ]

        result = recordset._read_group(
            groupby=("stage_id",), aggregates=("partner_id:recordset",)
        )

        self.assertEqual(len(result), 1)
        group_key, partner_rs = result[0]
        self.assertEqual(group_key, 1)
        self.assertIsInstance(partner_rs, OdooRecordset)
        self.assertEqual(partner_rs.model_name, "res.partner")
        self.assertEqual(partner_rs.ids, (7, 8))

    def test_read_group_offset_and_limit_forwarded_when_provided(self) -> None:
        recordset = OdooRecordset(
            self.executor, "sale.order", [], context={"lang": "en_US"}
        )
        self.executor.execute.return_value = [{"stage_id": 3}]

        recordset._read_group(
            groupby=("stage_id",), offset=5, limit=10, order="stage_id asc"
        )

        _call_kwargs = self.executor.execute.call_args.kwargs
        self.assertEqual(_call_kwargs["offset"], 5)
        self.assertEqual(_call_kwargs["limit"], 10)
        # read_group uses 'orderby', not 'order'
        self.assertEqual(_call_kwargs["orderby"], "stage_id asc")

    def test_read_group_zero_offset_not_forwarded(self) -> None:
        recordset = OdooRecordset(
            self.executor, "sale.order", [], context={"lang": "en_US"}
        )
        self.executor.execute.return_value = []

        recordset._read_group(groupby=("stage_id",), offset=0)

        _call_kwargs = self.executor.execute.call_args.kwargs
        self.assertNotIn("offset", _call_kwargs)

    # -- name_create -----------------------------------------------------------

    def test_name_create_returns_singleton_recordset(self) -> None:
        self.executor.execute.return_value = [42, "Test Name"]
        recordset = OdooRecordset(
            self.executor, "res.partner", [], context={"lang": "en_US"}
        )

        result = recordset.name_create("Test Name")

        self.assertIsInstance(result, OdooRecordset)
        self.assertEqual(result.ids, (42,))
        self.assertEqual(result.model_name, "res.partner")

    def test_name_create_forwards_context(self) -> None:
        self.executor.execute.return_value = [7, "Foo"]
        recordset = OdooRecordset(
            self.executor, "res.partner", [], context={"lang": "en_US"}
        )

        recordset.name_create("Foo")

        self.executor.execute.assert_called_once_with(
            "res.partner",
            "name_create",
            "Foo",
            context={"lang": "en_US"},
        )

    # -- name_search -----------------------------------------------------------

    def test_name_search_returns_list_of_id_name_pairs(self) -> None:
        self.executor.execute.return_value = [[1, "Foo"], [2, "Bar"]]
        recordset = OdooRecordset(
            self.executor, "res.partner", [], context={"lang": "en_US"}
        )

        result = recordset.name_search("foo")

        self.assertEqual(result, [[1, "Foo"], [2, "Bar"]])

    def test_name_search_forwards_domain_operator_limit_and_context(self) -> None:
        self.executor.execute.return_value = []
        recordset = OdooRecordset(
            self.executor, "res.partner", [], context={"lang": "en_US"}
        )

        recordset.name_search(
            "test", domain=[("active", "=", True)], operator="=", limit=5
        )

        self.executor.execute.assert_called_once_with(
            "res.partner",
            "name_search",
            "test",
            [("active", "=", True)],
            "=",
            5,
            context={"lang": "en_US"},
        )

    def test_name_search_empty_result_returns_empty_list(self) -> None:
        self.executor.execute.return_value = []
        recordset = OdooRecordset(
            self.executor, "res.partner", [], context={"lang": "en_US"}
        )

        result = recordset.name_search("no_match")

        self.assertEqual(result, [])

    def test_name_search_none_domain_passes_empty_list_to_server(self) -> None:
        self.executor.execute.return_value = []
        recordset = OdooRecordset(
            self.executor, "res.partner", [], context={"lang": "en_US"}
        )

        recordset.name_search("x", domain=None)

        _call_args = self.executor.execute.call_args.args
        self.assertEqual(_call_args[3], [])

    # -- default_get -----------------------------------------------------------

    def test_default_get_returns_dict_from_server(self) -> None:
        self.executor.execute.return_value = {"name": "Default"}
        recordset = OdooRecordset(
            self.executor, "res.partner", [], context={"lang": "en_US"}
        )

        result = recordset.default_get(["name", "active"])

        self.assertEqual(result, {"name": "Default"})
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "default_get",
            ["name", "active"],
            context={"lang": "en_US"},
        )

    def test_default_get_empty_dict_when_server_has_no_defaults(self) -> None:
        self.executor.execute.return_value = {}
        recordset = OdooRecordset(
            self.executor, "res.partner", [], context={"lang": "en_US"}
        )

        result = recordset.default_get(["name"])

        self.assertEqual(result, {})

    # -- copy ------------------------------------------------------------------

    def test_copy_returns_new_singleton_recordset(self) -> None:
        self.executor.execute.return_value = 99
        recordset = OdooRecordset(
            self.executor, "res.partner", [7], context={"lang": "en_US"}
        )

        result = recordset.copy()

        self.assertIsInstance(result, OdooRecordset)
        self.assertEqual(result.ids, (99,))
        self.assertNotEqual(result.ids, recordset.ids)

    def test_copy_forwards_default_dict_and_context(self) -> None:
        self.executor.execute.return_value = 100
        recordset = OdooRecordset(
            self.executor, "res.partner", [7], context={"lang": "en_US"}
        )

        recordset.copy(default={"name": "Copy"})

        self.executor.execute.assert_called_once_with(
            "res.partner",
            "copy",
            7,
            {"name": "Copy"},
            context={"lang": "en_US"},
        )

    def test_copy_none_default_sends_empty_dict_to_server(self) -> None:
        self.executor.execute.return_value = 101
        recordset = OdooRecordset(
            self.executor, "res.partner", [7], context={"lang": "en_US"}
        )

        recordset.copy(default=None)

        _call_args = self.executor.execute.call_args.args
        self.assertEqual(_call_args[3], {})

    def test_copy_raises_value_error_on_multi_record_recordset(self) -> None:
        recordset = OdooRecordset(
            self.executor, "res.partner", [1, 2], context={"lang": "en_US"}
        )

        with self.assertRaises(ValueError):
            recordset.copy()

    # -- get_metadata ----------------------------------------------------------

    def test_get_metadata_returns_list_of_audit_dicts(self) -> None:
        meta = [
            {
                "id": 1,
                "create_uid": [3, "Admin"],
                "create_date": "2024-01-01 00:00:00",
                "write_uid": [3, "Admin"],
                "write_date": "2024-06-01 00:00:00",
                "xmlid": False,
                "xmlids": [],
                "noupdate": False,
            },
            {
                "id": 2,
                "create_uid": [3, "Admin"],
                "create_date": "2024-01-02 00:00:00",
                "write_uid": [3, "Admin"],
                "write_date": "2024-06-02 00:00:00",
                "xmlid": False,
                "xmlids": [],
                "noupdate": False,
            },
        ]
        self.executor.execute.return_value = meta
        recordset = OdooRecordset(
            self.executor, "res.partner", [1, 2], context={"lang": "en_US"}
        )

        result = recordset.get_metadata()

        self.assertEqual(result, meta)
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "get_metadata",
            [1, 2],
            context={"lang": "en_US"},
        )

    def test_get_metadata_empty_recordset_passes_empty_list_to_server(self) -> None:
        self.executor.execute.return_value = []
        recordset = OdooRecordset(
            self.executor, "res.partner", [], context={"lang": "en_US"}
        )

        result = recordset.get_metadata()

        self.assertEqual(result, [])
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "get_metadata",
            [],
            context={"lang": "en_US"},
        )


class TestOdooRecordsetSetOperations(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = Mock(spec=OdooExecutor)

    def _rs(self, ids: int | Sequence[int], model: str = "res.partner"):
        return OdooRecordset(self.executor, model, ids, context={"lang": "en_US"})

    # ------------------------------------------------------------------
    # Union
    # ------------------------------------------------------------------

    def test_union_basic(self) -> None:
        result = self._rs([1, 2]) | self._rs([2, 3])
        self.assertEqual(result.ids, (1, 2, 3))

    def test_union_preserves_left_order_then_new_from_right(self) -> None:
        result = self._rs([3, 1]) | self._rs([2, 1, 4])
        self.assertEqual(result.ids, (3, 1, 2, 4))

    def test_union_both_empty(self) -> None:
        result = self._rs([]) | self._rs([])
        self.assertEqual(result.ids, ())

    def test_union_left_empty(self) -> None:
        result = self._rs([]) | self._rs([1, 2])
        self.assertEqual(result.ids, (1, 2))

    def test_union_right_empty(self) -> None:
        result = self._rs([1, 2]) | self._rs([])
        self.assertEqual(result.ids, (1, 2))

    def test_union_cross_model_raises(self) -> None:
        with self.assertRaises(ValueError):
            _ = self._rs([1], "res.partner") | self._rs([1], "res.users")

    def test_union_result_bound_to_left_env(self) -> None:
        result = self._rs([1, 2]) | self._rs([3])
        self.assertEqual(result.model_name, "res.partner")

    # ------------------------------------------------------------------
    # Intersection
    # ------------------------------------------------------------------

    def test_intersection_basic(self) -> None:
        result = self._rs([1, 2, 3]) & self._rs([2, 3, 4])
        self.assertEqual(result.ids, (2, 3))

    def test_intersection_preserves_left_order(self) -> None:
        result = self._rs([3, 2, 1]) & self._rs([1, 2])
        self.assertEqual(result.ids, (3, 2, 1) if False else (2, 1))
        # left order: 3 not in {1,2}, 2 in, 1 in  →  (2, 1)
        self.assertEqual(result.ids, (2, 1))

    def test_intersection_empty_result(self) -> None:
        result = self._rs([1, 2]) & self._rs([3, 4])
        self.assertEqual(result.ids, ())

    def test_intersection_both_empty(self) -> None:
        result = self._rs([]) & self._rs([])
        self.assertEqual(result.ids, ())

    def test_intersection_cross_model_raises(self) -> None:
        with self.assertRaises(ValueError):
            self._rs([1], "res.partner") & self._rs([1], "res.users")

    # ------------------------------------------------------------------
    # Difference
    # ------------------------------------------------------------------

    def test_difference_basic(self) -> None:
        result = self._rs([1, 2, 3]) - self._rs([2])
        self.assertEqual(result.ids, (1, 3))

    def test_difference_removes_all_right_ids(self) -> None:
        result = self._rs([1, 2, 3]) - self._rs([1, 2, 3])
        self.assertEqual(result.ids, ())

    def test_difference_right_empty(self) -> None:
        result = self._rs([1, 2]) - self._rs([])
        self.assertEqual(result.ids, (1, 2))

    def test_difference_both_empty(self) -> None:
        result = self._rs([]) - self._rs([])
        self.assertEqual(result.ids, ())

    def test_difference_cross_model_raises(self) -> None:
        with self.assertRaises(ValueError):
            self._rs([1], "res.partner") - self._rs([1], "res.users")

    # ------------------------------------------------------------------
    # Membership
    # ------------------------------------------------------------------

    def test_contains_true(self) -> None:
        rs = self._rs([1, 2, 3])
        self.assertIn(self._rs([2]), rs)

    def test_contains_false(self) -> None:
        rs = self._rs([1, 2, 3])
        self.assertNotIn(self._rs([99]), rs)

    def test_not_in(self) -> None:
        rs = self._rs([1, 2])
        self.assertNotIn(self._rs([5]), rs)

    def test_contains_non_singleton_raises(self) -> None:
        rs = self._rs([1, 2, 3])
        with self.assertRaises(ValueError):
            _ = self._rs([1, 2]) in rs

    def test_contains_non_recordset_raises(self) -> None:
        rs = self._rs([1, 2, 3])
        with self.assertRaises(TypeError):
            _ = 1 in rs  # type: ignore[operator]

    def test_contains_cross_model_raises(self) -> None:
        rs = self._rs([1, 2], "res.partner")
        with self.assertRaises(ValueError):
            _ = self._rs([1], "res.users") in rs

    # ------------------------------------------------------------------
    # Subset / superset
    # ------------------------------------------------------------------

    def test_subset_true(self) -> None:
        self.assertTrue(self._rs([1, 2]) <= self._rs([1, 2, 3]))

    def test_subset_equal_sets(self) -> None:
        self.assertTrue(self._rs([1, 2]) <= self._rs([1, 2]))

    def test_subset_false(self) -> None:
        self.assertFalse(self._rs([1, 4]) <= self._rs([1, 2, 3]))

    def test_strict_subset_true(self) -> None:
        self.assertTrue(self._rs([1, 2]) < self._rs([1, 2, 3]))

    def test_strict_subset_equal_sets_is_false(self) -> None:
        self.assertFalse(self._rs([1, 2]) < self._rs([1, 2]))

    def test_strict_subset_false(self) -> None:
        self.assertFalse(self._rs([1, 4]) < self._rs([1, 2, 3]))

    def test_superset_true(self) -> None:
        self.assertTrue(self._rs([1, 2, 3]) >= self._rs([1, 2]))

    def test_superset_equal_sets(self) -> None:
        self.assertTrue(self._rs([1, 2]) >= self._rs([1, 2]))

    def test_superset_false(self) -> None:
        self.assertFalse(self._rs([1, 2]) >= self._rs([1, 2, 3]))

    def test_strict_superset_true(self) -> None:
        self.assertTrue(self._rs([1, 2, 3]) > self._rs([1, 2]))

    def test_strict_superset_equal_sets_is_false(self) -> None:
        self.assertFalse(self._rs([1, 2]) > self._rs([1, 2]))

    def test_strict_superset_false(self) -> None:
        self.assertFalse(self._rs([1, 2]) > self._rs([1, 2, 3]))

    def test_subset_cross_model_raises(self) -> None:
        with self.assertRaises(ValueError):
            _ = self._rs([1], "res.partner") <= self._rs([1], "res.users")

    def test_superset_cross_model_raises(self) -> None:
        with self.assertRaises(ValueError):
            _ = self._rs([1, 2], "res.partner") >= self._rs([1], "res.users")


class TestOdooRecordsetWithCompany(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = Mock(spec=OdooExecutor)

    def test_with_company_returns_new_recordset(self) -> None:
        rs = OdooRecordset(self.executor, "res.partner", [7], context={"lang": "en_US"})
        derived = rs.with_company(3)
        self.assertIsNot(derived, rs)

    def test_with_company_derived_recordset_has_allowed_company_ids(self) -> None:
        rs = OdooRecordset(self.executor, "res.partner", [7], context={"lang": "en_US"})
        derived = rs.with_company(3)
        self.assertEqual(derived.context["allowed_company_ids"], [3])

    def test_with_company_original_env_context_unchanged(self) -> None:
        rs = OdooRecordset(self.executor, "res.partner", [7], context={"lang": "en_US"})
        rs.with_company(3)
        self.assertNotIn("allowed_company_ids", rs.context)

    def test_with_company_derived_recordset_same_model_and_ids(self) -> None:
        rs = OdooRecordset(
            self.executor, "res.partner", [7, 8], context={"lang": "en_US"}
        )
        derived = rs.with_company(3)
        self.assertEqual(derived.model_name, "res.partner")
        self.assertEqual(derived.ids, (7, 8))


class TestOdooRecordsetArchive(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = Mock(spec=OdooExecutor)

    def test_action_archive_writes_active_false(self) -> None:
        self.executor.execute.return_value = True
        rs = OdooRecordset(
            self.executor, "res.partner", [7, 8], context={"lang": "en_US"}
        )

        result = rs.action_archive()

        self.assertTrue(result)
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "write",
            [7, 8],
            {"active": False},
            context={"lang": "en_US"},
        )

    def test_action_unarchive_writes_active_true(self) -> None:
        self.executor.execute.return_value = True
        rs = OdooRecordset(
            self.executor, "res.partner", [7, 8], context={"lang": "en_US"}
        )

        result = rs.action_unarchive()

        self.assertTrue(result)
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "write",
            [7, 8],
            {"active": True},
            context={"lang": "en_US"},
        )

    def test_action_archive_returns_false_when_write_fails(self) -> None:
        self.executor.execute.return_value = False
        rs = OdooRecordset(self.executor, "res.partner", [7], context={"lang": "en_US"})
        self.assertFalse(rs.action_archive())

    def test_action_unarchive_returns_false_when_write_fails(self) -> None:
        self.executor.execute.return_value = False
        rs = OdooRecordset(self.executor, "res.partner", [7], context={"lang": "en_US"})
        self.assertFalse(rs.action_unarchive())
