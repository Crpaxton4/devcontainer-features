import unittest
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone

from odoo_sdk.fields.values import adapt_field_value, adapt_record_values
from odoo_sdk.fields.values import RelationCollection, RelationValue


class TestFieldAdapters(unittest.TestCase):
    def test_adapt_record_values_without_metadata_returns_plain_dict_copy(self) -> None:
        raw = {"name": "Acme"}

        result = adapt_record_values(raw, None)

        self.assertEqual(result, raw)
        self.assertIsNot(result, raw)

    def test_missing_metadata_returns_raw_value(self) -> None:
        raw = (7, "Acme")

        result = adapt_field_value(raw, None)

        self.assertIs(result, raw)

    def test_unsupported_field_type_returns_raw_value(self) -> None:
        raw = "Acme"

        result = adapt_field_value(raw, {"type": "char"})

        self.assertIs(result, raw)

    def test_many2one_tuple_becomes_relation_value(self) -> None:
        result = adapt_field_value(
            (7, "Acme"),
            {"type": "many2one", "relation": "res.partner"},
        )

        self.assertEqual(
            result,
            RelationValue(model_name="res.partner", id=7, label="Acme"),
        )

    def test_many2one_relation_value_is_idempotent(self) -> None:
        raw = RelationValue(model_name="res.partner", id=7, label="Acme")

        result = adapt_field_value(
            raw,
            {"type": "many2one", "relation": "res.partner"},
        )

        self.assertIs(result, raw)

    def test_many2one_integer_becomes_relation_value_without_label(self) -> None:
        result = adapt_field_value(
            7,
            {"type": "many2one", "relation": "res.partner"},
        )

        self.assertEqual(
            result,
            RelationValue(model_name="res.partner", id=7, label=None),
        )

    def test_many2one_empty_value_becomes_none(self) -> None:
        result = adapt_field_value(
            False,
            {"type": "many2one", "relation": "res.partner"},
        )

        self.assertIsNone(result)

    def test_many2one_missing_relation_falls_back_to_raw_value(self) -> None:
        raw = (7, "Acme")

        result = adapt_field_value(raw, {"type": "many2one"})

        self.assertIs(result, raw)

    def test_many2one_malformed_value_falls_back_to_raw_value(self) -> None:
        raw = ("seven", "Acme")

        result = adapt_field_value(
            raw,
            {"type": "many2one", "relation": "res.partner"},
        )

        self.assertIs(result, raw)

    def test_many2one_non_sequence_falls_back_to_raw_value(self) -> None:
        raw = {"id": 7}

        result = adapt_field_value(
            raw,
            {"type": "many2one", "relation": "res.partner"},
        )

        self.assertIs(result, raw)

    def test_many2one_empty_custom_sequence_becomes_none(self) -> None:
        result = adapt_field_value(
            range(0),
            {"type": "many2one", "relation": "res.partner"},
        )

        self.assertIsNone(result)

    def test_x2many_ids_become_relation_collection(self) -> None:
        result = adapt_field_value(
            [3, 1, 2],
            {"type": "many2many", "relation": "res.partner.category"},
        )

        self.assertEqual(
            result,
            RelationCollection("res.partner.category", (3, 1, 2)),
        )

    def test_x2many_relation_collection_is_idempotent(self) -> None:
        raw = RelationCollection("res.partner.category", (3, 1, 2))

        result = adapt_field_value(
            raw,
            {"type": "many2many", "relation": "res.partner.category"},
        )

        self.assertIs(result, raw)

    def test_x2many_empty_value_becomes_empty_relation_collection(self) -> None:
        result = adapt_field_value(
            [],
            {"type": "one2many", "relation": "res.partner.bank"},
        )

        self.assertEqual(
            result,
            RelationCollection("res.partner.bank", ()),
        )

    def test_x2many_missing_relation_falls_back_to_raw_value(self) -> None:
        raw = [1, 2]

        result = adapt_field_value(raw, {"type": "many2many"})

        self.assertIs(result, raw)

    def test_x2many_malformed_value_falls_back_to_raw_value(self) -> None:
        raw = [1, "2"]

        result = adapt_field_value(
            raw,
            {"type": "many2many", "relation": "res.partner.category"},
        )

        self.assertIs(result, raw)

    def test_x2many_non_sequence_falls_back_to_raw_value(self) -> None:
        raw = {"ids": [1, 2]}

        result = adapt_field_value(
            raw,
            {"type": "many2many", "relation": "res.partner.category"},
        )

        self.assertIs(result, raw)

    def test_date_string_becomes_python_date(self) -> None:
        result = adapt_field_value("2026-05-23", {"type": "date"})

        self.assertEqual(result, date(2026, 5, 23))

    def test_date_object_is_idempotent(self) -> None:
        raw = date(2026, 5, 23)

        result = adapt_field_value(raw, {"type": "date"})

        self.assertIs(result, raw)

    def test_date_empty_value_becomes_none(self) -> None:
        result = adapt_field_value("", {"type": "date"})

        self.assertIsNone(result)

    def test_date_parse_failure_falls_back_to_raw_value(self) -> None:
        raw = "23/05/2026"

        result = adapt_field_value(raw, {"type": "date"})

        self.assertIs(result, raw)

    def test_date_non_string_falls_back_to_raw_value(self) -> None:
        raw = 123

        result = adapt_field_value(raw, {"type": "date"})

        self.assertIs(result, raw)

    def test_datetime_string_becomes_utc_datetime(self) -> None:
        result = adapt_field_value("2026-05-23 10:15:00", {"type": "datetime"})

        self.assertEqual(
            result,
            datetime(2026, 5, 23, 10, 15, 0, tzinfo=timezone.utc),
        )

    def test_datetime_offset_value_is_normalized_to_utc(self) -> None:
        result = adapt_field_value(
            "2026-05-23T10:15:00+02:00",
            {"type": "datetime"},
        )

        self.assertEqual(
            result,
            datetime(2026, 5, 23, 8, 15, 0, tzinfo=timezone.utc),
        )

    def test_datetime_object_is_normalized_to_utc(self) -> None:
        raw = datetime(2026, 5, 23, 10, 15, 0)

        result = adapt_field_value(raw, {"type": "datetime"})

        self.assertEqual(
            result,
            datetime(2026, 5, 23, 10, 15, 0, tzinfo=timezone.utc),
        )

    def test_datetime_parse_failure_falls_back_to_raw_value(self) -> None:
        raw = "not-a-datetime"

        result = adapt_field_value(raw, {"type": "datetime"})

        self.assertIs(result, raw)

    def test_datetime_non_string_falls_back_to_raw_value(self) -> None:
        raw = 123

        result = adapt_field_value(raw, {"type": "datetime"})

        self.assertIs(result, raw)

    def test_binary_base64_string_becomes_bytes(self) -> None:
        result = adapt_field_value("aGVsbG8=", {"type": "binary"})

        self.assertEqual(result, b"hello")

    def test_binary_bytes_are_idempotent(self) -> None:
        raw = b"hello"

        result = adapt_field_value(raw, {"type": "binary"})

        self.assertIs(result, raw)

    def test_binary_empty_string_becomes_empty_bytes(self) -> None:
        result = adapt_field_value("", {"type": "binary"})

        self.assertEqual(result, b"")

    def test_binary_false_becomes_none(self) -> None:
        result = adapt_field_value(False, {"type": "binary"})

        self.assertIsNone(result)

    def test_binary_invalid_payload_falls_back_to_raw_value(self) -> None:
        raw = "not base64"

        result = adapt_field_value(raw, {"type": "binary"})

        self.assertIs(result, raw)

    def test_binary_non_string_falls_back_to_raw_value(self) -> None:
        raw = 123

        result = adapt_field_value(raw, {"type": "binary"})

        self.assertIs(result, raw)

    def test_record_adaptation_applies_per_field_metadata(self) -> None:
        result = adapt_record_values(
            {
                "name": "Acme",
                "parent_id": [7, "Parent"],
                "category_ids": [3, 1],
                "birthday": "2026-05-23",
                "image_128": "aGVsbG8=",
            },
            {
                "parent_id": {"type": "many2one", "relation": "res.partner"},
                "category_ids": {
                    "type": "many2many",
                    "relation": "res.partner.category",
                },
                "birthday": {"type": "date"},
                "image_128": {"type": "binary"},
            },
        )

        self.assertEqual(result["name"], "Acme")
        self.assertEqual(
            result["parent_id"],
            RelationValue(model_name="res.partner", id=7, label="Parent"),
        )
        self.assertEqual(
            result["category_ids"],
            RelationCollection("res.partner.category", (3, 1)),
        )
        self.assertEqual(result["birthday"], date(2026, 5, 23))
        self.assertEqual(result["image_128"], b"hello")

    def test_relation_collection_stores_tuple_ids(self) -> None:
        result = RelationCollection.from_ids("res.partner.category", [3, 1, 2])

        self.assertEqual(result.ids, (3, 1, 2))

    def test_relation_value_is_frozen(self) -> None:
        value = RelationValue(model_name="res.partner", id=7, label="Acme")

        with self.assertRaises(FrozenInstanceError):
            value.label = "Updated"  # type: ignore[misc]

    def test_relation_collection_is_frozen(self) -> None:
        value = RelationCollection(model_name="res.partner.category", ids=(3, 1, 2))

        with self.assertRaises(FrozenInstanceError):
            value.ids = (4, 5)  # type: ignore[misc]
