import unittest
from copy import deepcopy
from unittest.mock import Mock

from odoo_sdk.env.env import OdooEnv
from odoo_sdk.env.metadata_cache import MetadataCache
from odoo_sdk.records.recordset import OdooRecordset
from odoo_sdk.transport.executor import OdooExecutor


class TestOdooEnvInit(unittest.TestCase):
    def setUp(self):
        self.executor = Mock(spec=OdooExecutor)

    def test_executor_property_returns_bound_executor(self):
        env = OdooEnv(self.executor)
        self.assertIs(env.executor, self.executor)

    def test_context_defaults_to_empty_dict(self):
        env = OdooEnv(self.executor)
        self.assertEqual(env.context, {})

    def test_context_is_deep_copied_from_input(self):
        ctx = {"lang": "en_US"}
        env = OdooEnv(self.executor, ctx)
        ctx["lang"] = "fr_FR"
        self.assertEqual(env.context["lang"], "en_US")

    def test_metadata_cache_is_created_when_not_provided(self):
        env = OdooEnv(self.executor)
        self.assertIsInstance(env.metadata_cache, MetadataCache)

    def test_metadata_cache_is_shared_when_provided(self):
        shared_cache = MetadataCache()
        env = OdooEnv(self.executor, metadata_cache=shared_cache)
        self.assertIs(env.metadata_cache, shared_cache)

    def test_context_property_returns_defensive_copy(self):
        env = OdooEnv(self.executor, {"a": [1, 2]})
        ctx = env.context
        ctx["a"].append(99)
        self.assertEqual(env.context["a"], [1, 2])


class TestOdooEnvWithContext(unittest.TestCase):
    def setUp(self):
        self.executor = Mock(spec=OdooExecutor)

    def test_with_context_returns_new_env(self):
        env = OdooEnv(self.executor, {"lang": "en_US"})
        derived = env.with_context({"tz": "UTC"})
        self.assertIsNot(env, derived)

    def test_with_context_merges_context(self):
        env = OdooEnv(self.executor, {"lang": "en_US"})
        derived = env.with_context({"tz": "UTC"})
        self.assertEqual(derived.context["lang"], "en_US")
        self.assertEqual(derived.context["tz"], "UTC")

    def test_with_context_overrides_existing_key(self):
        env = OdooEnv(self.executor, {"lang": "en_US"})
        derived = env.with_context({"lang": "fr_FR"})
        self.assertEqual(derived.context["lang"], "fr_FR")
        self.assertEqual(env.context["lang"], "en_US")

    def test_with_context_shares_metadata_cache(self):
        env = OdooEnv(self.executor)
        derived = env.with_context({"lang": "en_US"})
        self.assertIs(derived.metadata_cache, env.metadata_cache)

    def test_with_context_does_not_mutate_parent(self):
        env = OdooEnv(self.executor, {"lang": "en_US"})
        env.with_context({"extra": "value"})
        self.assertNotIn("extra", env.context)


class TestOdooEnvWithCompany(unittest.TestCase):
    def setUp(self):
        self.executor = Mock(spec=OdooExecutor)

    def test_with_company_sets_allowed_company_ids(self):
        env = OdooEnv(self.executor)
        derived = env.with_company(5)
        self.assertEqual(derived.context["allowed_company_ids"], [5])

    def test_with_company_returns_new_env(self):
        env = OdooEnv(self.executor)
        derived = env.with_company(5)
        self.assertIsNot(env, derived)

    def test_with_company_does_not_mutate_parent(self):
        env = OdooEnv(self.executor)
        env.with_company(5)
        self.assertNotIn("allowed_company_ids", env.context)


class TestOdooEnvClearMetadataCache(unittest.TestCase):
    def setUp(self):
        self.executor = Mock(spec=OdooExecutor)
        self.executor.execute.return_value = {"name": {"type": "char"}}

    def test_clear_all_removes_cached_entries(self):
        env = OdooEnv(self.executor)
        env.get_field_metadata("res.partner")
        env.get_field_metadata("res.users")
        env.clear_metadata_cache()
        self.assertEqual(len(env.metadata_cache), 0)

    def test_clear_specific_model_removes_only_that_model(self):
        env = OdooEnv(self.executor)
        env.get_field_metadata("res.partner")
        env.get_field_metadata("res.users")
        env.clear_metadata_cache("res.partner")
        self.assertEqual(len(env.metadata_cache), 1)


class TestOdooEnvRecordValueCache(unittest.TestCase):
    def setUp(self):
        self.executor = Mock(spec=OdooExecutor)

    def test_get_missing_field_ids_returns_all_when_cache_is_empty(self):
        env = OdooEnv(self.executor)
        result = env.get_missing_field_ids("res.partner", [1, 2, 3], "name")
        self.assertEqual(result, [1, 2, 3])

    def test_get_missing_field_ids_skips_cached_records(self):
        env = OdooEnv(self.executor)
        env.cache_record_field_values("res.partner", 2, {"name": "Bob"})
        result = env.get_missing_field_ids("res.partner", [1, 2, 3], "name")
        self.assertEqual(result, [1, 3])

    def test_get_cached_field_value_returns_miss_when_not_cached(self):
        env = OdooEnv(self.executor)
        found, value = env.get_cached_field_value("res.partner", 1, "name")
        self.assertFalse(found)
        self.assertIsNone(value)

    def test_get_cached_field_value_returns_hit_when_cached(self):
        env = OdooEnv(self.executor)
        env.cache_record_field_values("res.partner", 1, {"name": "Alice"})
        found, value = env.get_cached_field_value("res.partner", 1, "name")
        self.assertTrue(found)
        self.assertEqual(value, "Alice")

    def test_cache_record_field_values_updates_existing_entry(self):
        env = OdooEnv(self.executor)
        env.cache_record_field_values("res.partner", 1, {"name": "Alice"})
        env.cache_record_field_values("res.partner", 1, {"email": "a@b.com"})
        _, name = env.get_cached_field_value("res.partner", 1, "name")
        _, email = env.get_cached_field_value("res.partner", 1, "email")
        self.assertEqual(name, "Alice")
        self.assertEqual(email, "a@b.com")

    def test_cache_record_field_values_noop_for_empty_values(self):
        env = OdooEnv(self.executor)
        env.cache_record_field_values("res.partner", 1, {})
        found, _ = env.get_cached_field_value("res.partner", 1, "name")
        self.assertFalse(found)


class TestOdooEnvGetFieldMetadata(unittest.TestCase):
    def setUp(self):
        self.executor = Mock(spec=OdooExecutor)

    def test_get_field_metadata_calls_executor(self):
        self.executor.execute.return_value = {"name": {"type": "char"}}
        env = OdooEnv(self.executor)
        result = env.get_field_metadata("res.partner")
        self.assertEqual(result, {"name": {"type": "char"}})
        self.executor.execute.assert_called_once_with(
            "res.partner", "fields_get"
        )

    def test_get_field_metadata_with_fields_passes_allfields(self):
        self.executor.execute.return_value = {}
        env = OdooEnv(self.executor)
        env.get_field_metadata("res.partner", fields=["name"])
        self.executor.execute.assert_called_once_with(
            "res.partner", "fields_get", allfields=["name"]
        )

    def test_get_field_metadata_with_attributes_passes_attributes(self):
        self.executor.execute.return_value = {}
        env = OdooEnv(self.executor)
        env.get_field_metadata("res.partner", attributes=["type"])
        self.executor.execute.assert_called_once_with(
            "res.partner", "fields_get", attributes=["type"]
        )

    def test_get_field_metadata_with_context_passes_context(self):
        self.executor.execute.return_value = {}
        env = OdooEnv(self.executor, {"lang": "en_US"})
        env.get_field_metadata("res.partner")
        self.executor.execute.assert_called_once_with(
            "res.partner", "fields_get", context={"lang": "en_US"}
        )

    def test_get_field_metadata_with_all_params(self):
        self.executor.execute.return_value = {}
        env = OdooEnv(self.executor, {"lang": "en_US"})
        env.get_field_metadata(
            "res.partner", fields=["name"], attributes=["type"]
        )
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "fields_get",
            allfields=["name"],
            attributes=["type"],
            context={"lang": "en_US"},
        )

    def test_get_field_metadata_refresh_reloads_from_executor(self):
        self.executor.execute.side_effect = [{"v": 1}, {"v": 2}]
        env = OdooEnv(self.executor)
        first = env.get_field_metadata("res.partner")
        refreshed = env.get_field_metadata("res.partner", refresh=True)
        self.assertEqual(first, {"v": 1})
        self.assertEqual(refreshed, {"v": 2})
        self.assertEqual(self.executor.execute.call_count, 2)

    def test_get_field_metadata_caches_result(self):
        self.executor.execute.return_value = {"name": {"type": "char"}}
        env = OdooEnv(self.executor)
        env.get_field_metadata("res.partner")
        env.get_field_metadata("res.partner")
        self.executor.execute.assert_called_once()


class TestOdooEnvRecordset(unittest.TestCase):
    def setUp(self):
        self.executor = Mock(spec=OdooExecutor)

    def test_getitem_returns_empty_recordset_for_model(self):
        env = OdooEnv(self.executor)
        rs = env["res.partner"]
        self.assertIsInstance(rs, OdooRecordset)
        self.assertEqual(rs.model_name, "res.partner")
        self.assertEqual(rs.ids, ())

    def test_recordset_factory_binds_model_and_ids(self):
        env = OdooEnv(self.executor)
        rs = env.recordset("res.partner", [1, 2, 3])
        self.assertIsInstance(rs, OdooRecordset)
        self.assertEqual(rs.model_name, "res.partner")
        self.assertEqual(rs.ids, (1, 2, 3))

    def test_recordset_factory_with_int_id(self):
        env = OdooEnv(self.executor)
        rs = env.recordset("res.partner", 7)
        self.assertEqual(rs.ids, (7,))

    def test_recordset_factory_empty_by_default(self):
        env = OdooEnv(self.executor)
        rs = env.recordset("res.partner")
        self.assertEqual(rs.ids, ())
