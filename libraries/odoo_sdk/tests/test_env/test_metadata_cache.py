import unittest

from odoo_sdk.env.metadata_cache import (
    MetadataCache,
    _freeze_context_value,
)


class TestFreezeContextValue(unittest.TestCase):
    def test_scalar_is_returned_unchanged(self):
        self.assertEqual(_freeze_context_value(42), 42)
        self.assertEqual(_freeze_context_value("hello"), "hello")
        self.assertIsNone(_freeze_context_value(None))

    def test_dict_is_frozen_to_sorted_tuple(self):
        result = _freeze_context_value({"b": 2, "a": 1})
        self.assertEqual(result, (("a", 1), ("b", 2)))

    def test_nested_dict_is_frozen_recursively(self):
        result = _freeze_context_value({"key": {"inner": "val"}})
        self.assertEqual(result, (("key", (("inner", "val"),)),))

    def test_list_is_frozen_to_tuple(self):
        result = _freeze_context_value([1, 2, 3])
        self.assertEqual(result, (1, 2, 3))

    def test_tuple_is_frozen_to_tuple(self):
        result = _freeze_context_value((4, 5))
        self.assertEqual(result, (4, 5))

    def test_set_is_frozen_to_sorted_tuple(self):
        result = _freeze_context_value({10, 20})
        self.assertIn(result, [(10, 20), (20, 10)])
        self.assertIsInstance(result, tuple)

    def test_nested_list_in_dict_frozen_recursively(self):
        result = _freeze_context_value({"ids": [1, 2]})
        self.assertEqual(result, (("ids", (1, 2)),))


class TestMetadataCacheLen(unittest.TestCase):
    def test_len_is_zero_initially(self):
        cache = MetadataCache()
        self.assertEqual(len(cache), 0)

    def test_len_increments_with_entries(self):
        cache = MetadataCache()
        cache.get_or_load("res.partner", loader=lambda: {"name": {}})
        self.assertEqual(len(cache), 1)
        cache.get_or_load("res.users", loader=lambda: {"login": {}})
        self.assertEqual(len(cache), 2)


class TestMetadataCacheClearModel(unittest.TestCase):
    def test_clear_specific_model_keeps_other_models(self):
        cache = MetadataCache()
        cache.get_or_load("res.partner", loader=lambda: {"name": {}})
        cache.get_or_load("res.users", loader=lambda: {"login": {}})
        cache.clear(model_name="res.partner")
        self.assertEqual(len(cache), 1)

    def test_clear_specific_model_forces_reload_for_that_model(self):
        calls = {"count": 0}

        def loader():
            calls["count"] += 1
            return {"name": {}}

        cache = MetadataCache()
        cache.get_or_load("res.partner", loader=loader)
        cache.clear(model_name="res.partner")
        cache.get_or_load("res.partner", loader=loader)
        self.assertEqual(calls["count"], 2)

    def test_clear_all_removes_every_entry(self):
        cache = MetadataCache()
        cache.get_or_load("res.partner", loader=lambda: {})
        cache.get_or_load("res.users", loader=lambda: {})
        cache.clear()
        self.assertEqual(len(cache), 0)

    def test_clear_nonexistent_model_leaves_cache_intact(self):
        cache = MetadataCache()
        cache.get_or_load("res.partner", loader=lambda: {"name": {}})
        cache.clear(model_name="res.users")
        self.assertEqual(len(cache), 1)

    def test_get_or_load_with_nested_context_caches_correctly(self):
        cache = MetadataCache()
        ctx = {"ids": [1, 2], "nested": {"key": "val"}}
        calls = {"count": 0}

        def loader():
            calls["count"] += 1
            return {"f": {}}

        cache.get_or_load("res.partner", context=ctx, loader=loader)
        cache.get_or_load("res.partner", context=ctx, loader=loader)
        self.assertEqual(calls["count"], 1)
