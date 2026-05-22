import unittest
from unittest.mock import Mock

from odoo_sdk.odoo_service.odoo_env import OdooEnv
from odoo_sdk.odoo_service.odoo_executor import OdooExecutor
from odoo_sdk.odoo_service.odoo_model import OdooModel


class TestOdooEnv(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = Mock(spec=OdooExecutor)

    def test_empty_context_defaults_to_empty_dict(self) -> None:
        env = OdooEnv(self.executor)

        self.assertEqual(env.context, {})

    def test_constructor_defensively_copies_input_context(self) -> None:
        input_context = {"lang": "en_US"}

        env = OdooEnv(self.executor, input_context)
        input_context["lang"] = "fr_FR"

        self.assertEqual(env.context, {"lang": "en_US"})

    def test_constructor_defensively_copies_nested_context_values(self) -> None:
        input_context = {"allowed_company_ids": [1, 2]}

        env = OdooEnv(self.executor, input_context)
        input_context["allowed_company_ids"].append(3)

        self.assertEqual(env.context, {"allowed_company_ids": [1, 2]})

    def test_context_property_returns_defensive_copy(self) -> None:
        env = OdooEnv(self.executor, {"lang": "en_US"})

        context = env.context
        context["lang"] = "fr_FR"

        self.assertEqual(env.context, {"lang": "en_US"})

    def test_context_property_returns_nested_defensive_copy(self) -> None:
        env = OdooEnv(self.executor, {"allowed_company_ids": [1, 2]})

        context = env.context
        context["allowed_company_ids"].append(3)

        self.assertEqual(env.context, {"allowed_company_ids": [1, 2]})

    def test_with_context_returns_new_env_without_mutating_parent(self) -> None:
        env = OdooEnv(self.executor, {"lang": "en_US"})

        derived_env = env.with_context({"tz": "UTC"})

        self.assertIsNot(env, derived_env)
        self.assertEqual(env.context, {"lang": "en_US"})
        self.assertEqual(derived_env.context, {"lang": "en_US", "tz": "UTC"})

    def test_derived_env_context_mutation_does_not_leak_to_parent(self) -> None:
        env = OdooEnv(self.executor, {"lang": "en_US"})
        derived_env = env.with_context({"tz": "UTC"})

        context = derived_env.context
        context["lang"] = "fr_FR"
        context["tz"] = "Europe/Paris"

        self.assertEqual(env.context, {"lang": "en_US"})
        self.assertEqual(derived_env.context, {"lang": "en_US", "tz": "UTC"})

    def test_with_context_merges_values_across_derivations(self) -> None:
        env = OdooEnv(self.executor, {"lang": "en_US"})

        derived_env = env.with_context({"tz": "UTC"}).with_context(
            {"active_test": False}
        )

        self.assertEqual(
            derived_env.context,
            {"lang": "en_US", "tz": "UTC", "active_test": False},
        )

    def test_model_lookup_returns_model_bound_to_same_executor(self) -> None:
        env = OdooEnv(self.executor, {"lang": "en_US"})

        model = env["res.partner"]

        self.assertIsInstance(model, OdooModel)
        self.assertIs(model.client, self.executor)
        self.assertEqual(model.name, "res.partner")