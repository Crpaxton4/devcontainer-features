import unittest
from dataclasses import FrozenInstanceError

from hypothesis import given, strategies

from odoo_sdk.fields.commands import X2ManyCommand as Command
from odoo_sdk.fields.commands import (
    _is_placeholder,
    _normalize_id_payload,
    _normalize_mapping_payload,
    _validate_record_id,
    normalize_x2many_commands,
)


class TestX2ManyCommands(unittest.TestCase):
    def test_internal_helpers_require_keyword_only_operation(self) -> None:
        with self.assertRaises(TypeError):
            _normalize_mapping_payload({"name": "Acme"}, "create")

        with self.assertRaises(TypeError):
            _normalize_id_payload([1, 2], "set")

        with self.assertRaises(TypeError):
            _validate_record_id(7, "link")

    def test_helper_is_frozen_and_slotted(self) -> None:
        command = Command.link(7)

        with self.assertRaises(FrozenInstanceError):
            command.record_id = 9

        self.assertFalse(hasattr(command, "__dict__"))

    def test_create_helper_deep_copies_mutable_payload(self) -> None:
        values = {"name": ["Acme"]}
        command = Command.create(values)

        values["name"].append("Mutated")
        payload = command.serialize()
        payload[2]["name"].append("Changed")

        self.assertEqual(command.serialize(), (0, 0, {"name": ["Acme"]}))
        self.assertEqual(command.record_id, 0)

    def test_manual_create_command_resets_record_id(self) -> None:
        command = Command(0, record_id=99, payload={"name": "Acme"})

        self.assertEqual(command.record_id, 0)
        self.assertEqual(command.serialize(), (0, 0, {"name": "Acme"}))

    def test_update_helper_serializes_to_canonical_tuple(self) -> None:
        command = Command.update(7, {"name": "Updated"})

        self.assertEqual(command.serialize(), (1, 7, {"name": "Updated"}))

    def test_delete_helper_serializes_to_canonical_tuple(self) -> None:
        command = Command.delete(7)

        self.assertEqual(command.serialize(), (2, 7, 0))

    def test_unlink_helper_serializes_to_canonical_tuple(self) -> None:
        command = Command.unlink(7)

        self.assertEqual(command.serialize(), (3, 7, 0))

    def test_link_helper_serializes_to_canonical_tuple(self) -> None:
        command = Command.link(7)

        self.assertEqual(command.serialize(), (4, 7, 0))

    def test_clear_helper_serializes_to_canonical_tuple(self) -> None:
        command = Command.clear()

        self.assertEqual(command.serialize(), (5, 0, 0))

    def test_manual_clear_command_resets_record_id_and_payload(self) -> None:
        command = Command(5, record_id=9, payload=1)

        self.assertEqual(command.record_id, 0)
        self.assertEqual(command.payload, 0)
        self.assertEqual(command.serialize(), (5, 0, 0))

    @given(
        strategies.lists(strategies.integers(min_value=1, max_value=999), max_size=5)
    )
    def test_set_helper_preserves_id_order(self, ids: list[int]) -> None:
        command = Command.set(ids)

        self.assertEqual(command.serialize(), (6, 0, ids))
        self.assertEqual(command.record_id, 0)

    def test_manual_set_command_resets_record_id(self) -> None:
        command = Command(6, record_id=8, payload=[3, 1])

        self.assertEqual(command.record_id, 0)
        self.assertEqual(command.payload, (3, 1))
        self.assertEqual(command.serialize(), (6, 0, [3, 1]))

    def test_normalize_single_helper_returns_one_command_list(self) -> None:
        result = normalize_x2many_commands(Command.link(4))

        self.assertEqual(result, [(4, 4, 0)])

    def test_normalize_raw_create_tuple(self) -> None:
        result = normalize_x2many_commands((0, 0, {"name": "Acme"}))

        self.assertEqual(result, [(0, 0, {"name": "Acme"})])

    def test_normalize_raw_update_tuple_uses_record_id_and_payload_slots(self) -> None:
        result = normalize_x2many_commands((1, 7, {"name": "Updated"}))

        self.assertEqual(result, [(1, 7, {"name": "Updated"})])

    def test_normalize_raw_relation_commands_accept_short_and_canonical_forms(
        self,
    ) -> None:
        self.assertEqual(normalize_x2many_commands((2, 9)), [(2, 9, 0)])
        self.assertEqual(normalize_x2many_commands((3, 8, 0)), [(3, 8, 0)])
        self.assertEqual(normalize_x2many_commands((4, 7, False)), [(4, 7, 0)])

    def test_normalize_raw_clear_tuple_accepts_all_supported_arity_forms(self) -> None:
        self.assertEqual(normalize_x2many_commands((5,)), [(5, 0, 0)])
        self.assertEqual(normalize_x2many_commands((5, 0)), [(5, 0, 0)])
        self.assertEqual(normalize_x2many_commands((5, 0, None)), [(5, 0, 0)])

    def test_normalize_raw_set_tuple(self) -> None:
        result = normalize_x2many_commands((6, 0, [3, 1]))

        self.assertEqual(result, [(6, 0, [3, 1])])

    def test_normalize_mixed_helper_and_raw_commands(self) -> None:
        result = normalize_x2many_commands(
            [
                Command.clear(),
                (4, 7),
                Command.set([3, 1]),
            ]
        )

        self.assertEqual(result, [(5, 0, 0), (4, 7, 0), (6, 0, [3, 1])])

    def test_normalize_short_raw_tuple_forms_to_canonical_shape(self) -> None:
        result = normalize_x2many_commands(
            [
                (5,),
                (2, 9),
                (3, 4),
            ]
        )

        self.assertEqual(result, [(5, 0, 0), (2, 9, 0), (3, 4, 0)])

    def test_rejects_non_mapping_create_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "mapping payload"):
            Command.create([("name", "Acme")])

    def test_rejects_non_positive_record_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive integer id"):
            Command.link(0)

        with self.assertRaisesRegex(ValueError, "positive integer id"):
            Command.unlink(-1)

        with self.assertRaisesRegex(ValueError, "positive integer id"):
            Command.delete(True)

    def test_rejects_non_iterable_set_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "iterable of ids"):
            Command.set(7)

    def test_rejects_string_and_mapping_set_payloads(self) -> None:
        with self.assertRaisesRegex(ValueError, "iterable of ids"):
            Command.set("7")

        with self.assertRaisesRegex(ValueError, "iterable of ids"):
            Command.set({"id": 7})

    def test_rejects_invalid_set_item_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive integer id"):
            Command.set([1, 0])

        with self.assertRaisesRegex(ValueError, "positive integer id"):
            Command.set([1, -1])

    def test_placeholder_helper_accepts_only_none_false_or_zero(self) -> None:
        class FalseLike:
            def __eq__(self, other: object) -> bool:
                return other is False

            def __le__(self, other: object) -> bool:
                return other is False

        self.assertTrue(_is_placeholder(None))
        self.assertTrue(_is_placeholder(False))
        self.assertTrue(_is_placeholder(0))

        self.assertFalse(_is_placeholder(True))
        self.assertFalse(_is_placeholder(-1))
        self.assertFalse(_is_placeholder(1))
        self.assertFalse(_is_placeholder(FalseLike()))

    def test_clear_tuples_reject_negative_placeholder_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "second item"):
            normalize_x2many_commands((5, -1))

        with self.assertRaisesRegex(ValueError, "third item"):
            normalize_x2many_commands((5, 0, -1))

    def test_rejects_malformed_raw_update_tuple(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly 3 items"):
            normalize_x2many_commands((1, 7))

        with self.assertRaisesRegex(ValueError, "exactly 3 items"):
            normalize_x2many_commands((1, 7, {"name": "Updated"}, 0))

    def test_rejects_malformed_raw_create_tuple(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly 3 items"):
            normalize_x2many_commands((0, 0))

        with self.assertRaisesRegex(ValueError, "exactly 3 items"):
            normalize_x2many_commands((0, 0, {"name": "Acme"}, 1))

        with self.assertRaisesRegex(ValueError, "second item"):
            normalize_x2many_commands((0, 1, {"name": "Acme"}))

        with self.assertRaisesRegex(ValueError, "second item"):
            normalize_x2many_commands((0, True, {"name": "Acme"}))

    def test_rejects_malformed_raw_relation_id_tuple(self) -> None:
        with self.assertRaisesRegex(ValueError, "2 or 3 items"):
            normalize_x2many_commands((2, 9, 0, 0))

        with self.assertRaisesRegex(ValueError, "third item"):
            normalize_x2many_commands((4, 7, 1))

        with self.assertRaisesRegex(ValueError, "third item"):
            normalize_x2many_commands((3, 8, True))

    def test_rejects_malformed_raw_clear_tuple(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 1 and 3 items"):
            normalize_x2many_commands((5, 0, 0, 0))

        with self.assertRaisesRegex(ValueError, "second item"):
            normalize_x2many_commands((5, 1))

        with self.assertRaisesRegex(ValueError, "second item"):
            normalize_x2many_commands((5, True))

        with self.assertRaisesRegex(ValueError, "third item"):
            normalize_x2many_commands((5, 0, 1))

        with self.assertRaisesRegex(ValueError, "third item"):
            normalize_x2many_commands((5, 0, True))

    def test_rejects_malformed_raw_set_tuple(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly 3 items"):
            normalize_x2many_commands((6, 0))

        with self.assertRaisesRegex(ValueError, "exactly 3 items"):
            normalize_x2many_commands((6, 0, [1], 0))

        with self.assertRaisesRegex(ValueError, "second item"):
            normalize_x2many_commands((6, 1, [1]))

        with self.assertRaisesRegex(ValueError, "second item"):
            normalize_x2many_commands((6, True, [1]))

        with self.assertRaisesRegex(ValueError, "positive integer id"):
            normalize_x2many_commands((6, 0, [1, 0]))

    def test_rejects_non_integer_and_unsupported_raw_command_codes(self) -> None:
        with self.assertRaisesRegex(ValueError, "integer code"):
            normalize_x2many_commands((True, 0, {}))

        with self.assertRaisesRegex(ValueError, "Unsupported x2many command code"):
            normalize_x2many_commands((7, 0, {}))

    def test_rejects_empty_or_non_sequence_top_level_inputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            normalize_x2many_commands([])

        with self.assertRaisesRegex(ValueError, "field values must be"):
            normalize_x2many_commands("abc")

        with self.assertRaisesRegex(ValueError, "field values must be"):
            normalize_x2many_commands(b"abc")

        with self.assertRaisesRegex(ValueError, "field values must be"):
            normalize_x2many_commands({"code": 1})

    def test_rejects_non_command_sequence_items(self) -> None:
        with self.assertRaisesRegex(ValueError, "helper objects or raw tuples"):
            normalize_x2many_commands([1])
