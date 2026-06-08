from copy import deepcopy
import unittest
from unittest.mock import Mock

from hypothesis import given, strategies

import odoo_sdk.env.env as odoo_env_module
from odoo_sdk.env.env import OdooEnv
from odoo_sdk.transport.executor import OdooExecutor
from odoo_sdk.records.model import OdooModel
from odoo_sdk.records.recordset import OdooRecordset


CONTEXT_KEYS = strategies.text(min_size=1, max_size=20)
CONTEXT_SCALARS = (
    strategies.none()
    | strategies.booleans()
    | strategies.integers()
    | strategies.text(max_size=40)
)
CONTEXT_VALUES = strategies.recursive(
    CONTEXT_SCALARS,
    lambda children: strategies.lists(children, max_size=4)
    | strategies.dictionaries(CONTEXT_KEYS, children, max_size=4),
    max_leaves=12,
)
CONTEXTS = strategies.dictionaries(CONTEXT_KEYS, CONTEXT_VALUES, max_size=4)
MUTABLE_CONTEXT_VALUES = strategies.recursive(
    strategies.lists(CONTEXT_SCALARS, min_size=1, max_size=3)
    | strategies.dictionaries(CONTEXT_KEYS, CONTEXT_SCALARS, min_size=1, max_size=3),
    lambda children: strategies.lists(children, min_size=1, max_size=3)
    | strategies.dictionaries(CONTEXT_KEYS, children, min_size=1, max_size=3),
    max_leaves=8,
)
MUTABLE_CONTEXTS = strategies.dictionaries(
    CONTEXT_KEYS, MUTABLE_CONTEXT_VALUES, min_size=1, max_size=4
)


def _mutate_first_nested_value(context: dict[str, object]) -> None:
    for value in context.values():
        if isinstance(value, list):
            value.append("__mutated__")
            return
        if isinstance(value, dict):
            value["__mutated__"] = "__mutated__"
            return
    raise AssertionError("expected a mutable nested value")


def _expected_merged_context(
    base_context: dict[str, object], extra_context: dict[str, object]
) -> dict[str, object]:
    merged = deepcopy(base_context)
    merged.update(deepcopy(extra_context))
    return merged


class TestOdooEnv(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = Mock(spec=OdooExecutor)

    def test_empty_context_defaults_to_empty_dict(self) -> None:
        env = OdooEnv(self.executor)

        self.assertEqual(env.context, {})

    def test_type_checking_symbols_are_not_runtime_imported(self) -> None:
        self.assertFalse(hasattr(odoo_env_module, "OdooModel"))
        self.assertFalse(hasattr(odoo_env_module, "OdooRecordset"))

    @given(MUTABLE_CONTEXTS)
    def test_constructor_defensively_copies_input_context(
        self, input_context: dict[str, object]
    ) -> None:
        expected_context = deepcopy(input_context)
        env = OdooEnv(self.executor, input_context)

        _mutate_first_nested_value(input_context)

        self.assertEqual(env.context, expected_context)

    @given(MUTABLE_CONTEXTS)
    def test_context_property_returns_defensive_copy(
        self, input_context: dict[str, object]
    ) -> None:
        env = OdooEnv(self.executor, input_context)
        expected_context = deepcopy(input_context)

        context = env.context
        _mutate_first_nested_value(context)

        self.assertEqual(env.context, expected_context)

    @given(CONTEXTS, CONTEXTS)
    def test_with_context_returns_new_env_without_mutating_parent(
        self,
        base_context: dict[str, object],
        extra_context: dict[str, object],
    ) -> None:
        env = OdooEnv(self.executor, base_context)
        expected_parent_context = deepcopy(base_context)

        derived_env = env.with_context(extra_context)

        self.assertIsNot(env, derived_env)
        self.assertEqual(env.context, expected_parent_context)
        self.assertEqual(
            derived_env.context,
            _expected_merged_context(base_context, extra_context),
        )

    @given(CONTEXTS, MUTABLE_CONTEXTS)
    def test_derived_env_context_mutation_does_not_leak_to_parent(
        self,
        base_context: dict[str, object],
        extra_context: dict[str, object],
    ) -> None:
        env = OdooEnv(self.executor, base_context)
        derived_env = env.with_context(extra_context)
        expected_parent_context = deepcopy(base_context)
        expected_derived_context = _expected_merged_context(base_context, extra_context)

        context = derived_env.context
        _mutate_first_nested_value(context)

        self.assertEqual(env.context, expected_parent_context)
        self.assertEqual(derived_env.context, expected_derived_context)

    @given(CONTEXTS, MUTABLE_CONTEXTS)
    def test_with_context_defensively_copies_overlay_context(
        self,
        base_context: dict[str, object],
        extra_context: dict[str, object],
    ) -> None:
        env = OdooEnv(self.executor, base_context)
        expected_derived_context = _expected_merged_context(base_context, extra_context)

        derived_env = env.with_context(extra_context)
        _mutate_first_nested_value(extra_context)

        self.assertEqual(derived_env.context, expected_derived_context)

    @given(
        CONTEXT_KEYS,
        CONTEXTS,
        CONTEXT_VALUES,
        CONTEXT_VALUES,
    )
    def test_with_context_prefers_override_values_for_shared_keys(
        self,
        shared_key: str,
        base_context: dict[str, object],
        base_value: object,
        override_value: object,
    ) -> None:
        env = OdooEnv(self.executor, {**base_context, shared_key: base_value})

        derived_env = env.with_context({shared_key: override_value})

        self.assertEqual(env.context[shared_key], deepcopy(base_value))
        self.assertEqual(derived_env.context[shared_key], deepcopy(override_value))

    @given(CONTEXTS, CONTEXTS, CONTEXTS)
    def test_with_context_merges_values_across_derivations(
        self,
        base_context: dict[str, object],
        first_overlay: dict[str, object],
        second_overlay: dict[str, object],
    ) -> None:
        env = OdooEnv(self.executor, base_context)

        derived_env = env.with_context(first_overlay).with_context(second_overlay)

        expected_context = _expected_merged_context(
            _expected_merged_context(base_context, first_overlay),
            second_overlay,
        )

        self.assertEqual(derived_env.context, expected_context)

    def test_with_context_preserves_shared_metadata_cache_reference(self) -> None:
        env = OdooEnv(self.executor, {"lang": "en_US"})

        derived_env = env.with_context({"tz": "UTC"})

        self.assertIs(derived_env.metadata_cache, env.metadata_cache)

    def test_get_field_metadata_reuses_cache_for_equivalent_derived_envs(self) -> None:
        self.executor.execute.return_value = {"name": {"type": "char"}}
        env = OdooEnv(self.executor, {"lang": "en_US"})

        first = env.get_field_metadata("res.partner", ["name"], ["type"])
        second = env.with_context({}).get_field_metadata(
            "res.partner",
            ["name"],
            ["type"],
        )

        self.assertEqual(first, second)
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "fields_get",
            allfields=["name"],
            attributes=["type"],
            context={"lang": "en_US"},
        )

    def test_get_field_metadata_isolated_by_context(self) -> None:
        self.executor.execute.side_effect = [
            {"name": {"string": "Name"}},
            {"name": {"string": "Nom"}},
        ]
        env = OdooEnv(self.executor, {"lang": "en_US"})

        english = env.get_field_metadata("res.partner", ["name"], ["string"])
        french = env.with_context({"lang": "fr_FR"}).get_field_metadata(
            "res.partner",
            ["name"],
            ["string"],
        )

        self.assertEqual(english, {"name": {"string": "Name"}})
        self.assertEqual(french, {"name": {"string": "Nom"}})
        self.assertEqual(self.executor.execute.call_count, 2)

    def test_clear_metadata_cache_removes_cached_entries(self) -> None:
        self.executor.execute.side_effect = [
            {"name": {"type": "char"}},
            {"name": {"type": "char", "string": "Name"}},
        ]
        env = OdooEnv(self.executor)

        env.get_field_metadata("res.partner", ["name"], ["type"])
        env.clear_metadata_cache("res.partner")
        result = env.get_field_metadata("res.partner", ["name"], ["type"])

        self.assertEqual(result, {"name": {"type": "char", "string": "Name"}})
        self.assertEqual(self.executor.execute.call_count, 2)

    def test_get_field_metadata_refresh_bypasses_cached_entry(self) -> None:
        self.executor.execute.side_effect = [
            {"name": {"type": "char"}},
            {"name": {"type": "char", "string": "Name"}},
        ]
        env = OdooEnv(self.executor)

        cached = env.get_field_metadata("res.partner", ["name"], ["type"])
        refreshed = env.get_field_metadata(
            "res.partner",
            ["name"],
            ["type"],
            refresh=True,
        )

        self.assertEqual(cached, {"name": {"type": "char"}})
        self.assertEqual(refreshed, {"name": {"type": "char", "string": "Name"}})
        self.assertEqual(self.executor.execute.call_count, 2)

    def test_clear_metadata_cache_without_model_name_removes_all_entries(self) -> None:
        self.executor.execute.side_effect = [
            {"name": {"type": "char"}},
            {"login": {"type": "char"}},
            {"name": {"type": "char", "string": "Name"}},
            {"login": {"type": "char", "string": "Login"}},
        ]
        env = OdooEnv(self.executor)

        env.get_field_metadata("res.partner", ["name"], ["type"])
        env.get_field_metadata("res.users", ["login"], ["type"])
        env.clear_metadata_cache()
        partner = env.get_field_metadata("res.partner", ["name"], ["type"])
        user = env.get_field_metadata("res.users", ["login"], ["type"])

        self.assertEqual(partner, {"name": {"type": "char", "string": "Name"}})
        self.assertEqual(user, {"login": {"type": "char", "string": "Login"}})
        self.assertEqual(self.executor.execute.call_count, 4)

    @given(strategies.text())
    def test_model_lookup_returns_model_bound_recordset(
        self, model_name: str
    ) -> None:
        env = OdooEnv(self.executor, {"lang": "en_US"})

        model = env[model_name]

        self.assertIsInstance(model, OdooRecordset)
        self.assertEqual(model.model_name, model_name)
        self.assertEqual(model.ids, ())
        self.assertIs(model.env, env)

    @given(strategies.text(), strategies.lists(strategies.integers(), max_size=4))
    def test_recordset_helper_returns_env_bound_recordset(
        self, model_name: str, ids: list[int]
    ) -> None:
        env = OdooEnv(self.executor, {"lang": "en_US"})

        recordset = env.recordset(model_name, ids)

        self.assertIsInstance(recordset, OdooRecordset)
        self.assertIs(recordset.env, env)
        self.assertEqual(recordset.model_name, model_name)
        self.assertEqual(recordset.ids, tuple(ids))
