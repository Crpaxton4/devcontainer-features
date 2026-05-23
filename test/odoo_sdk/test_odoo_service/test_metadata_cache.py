import unittest
from unittest.mock import Mock

from odoo_sdk.odoo_service.metadata_cache import MetadataCache


class TestMetadataCache(unittest.TestCase):
    def test_reuses_equivalent_requests_with_mixed_ordering(self) -> None:
        cache = MetadataCache()
        loader = Mock(return_value={"name": {"type": "char"}})

        first = cache.get_or_load(
            "res.partner",
            fields=["name", "email"],
            attributes=["type", "string"],
            context={"lang": "en_US", "allowed_company_ids": [2, 1]},
            loader=loader,
        )
        second = cache.get_or_load(
            "res.partner",
            fields=["email", "name", "name"],
            attributes=["string", "type"],
            context={"allowed_company_ids": [2, 1], "lang": "en_US"},
            loader=loader,
        )

        self.assertEqual(first, second)
        loader.assert_called_once_with()

    def test_omitted_and_empty_requests_do_not_share_cache_keys(self) -> None:
        cache = MetadataCache()
        loader = Mock(
            side_effect=[
                {"name": {"type": "char"}},
                {"name": {"type": "char", "string": "Name"}},
            ]
        )

        omitted = cache.get_or_load("res.partner", loader=loader)
        explicit_empty = cache.get_or_load(
            "res.partner",
            fields=[],
            loader=loader,
        )

        self.assertNotEqual(omitted, explicit_empty)
        self.assertEqual(loader.call_count, 2)

    def test_failed_load_does_not_poison_cache(self) -> None:
        cache = MetadataCache()
        loader = Mock(
            side_effect=[
                RuntimeError("boom"),
                {"name": {"type": "char"}},
            ]
        )

        with self.assertRaisesRegex(RuntimeError, "boom"):
            cache.get_or_load("res.partner", loader=loader)

        result = cache.get_or_load("res.partner", loader=loader)

        self.assertEqual(result, {"name": {"type": "char"}})
        self.assertEqual(loader.call_count, 2)

    def test_refresh_reloads_without_discarding_previous_success(self) -> None:
        cache = MetadataCache()
        loader = Mock(
            side_effect=[
                {"name": {"type": "char"}},
                RuntimeError("boom"),
            ]
        )

        cached = cache.get_or_load("res.partner", loader=loader)

        with self.assertRaisesRegex(RuntimeError, "boom"):
            cache.get_or_load("res.partner", refresh=True, loader=loader)

        reused = cache.get_or_load("res.partner", loader=loader)

        self.assertEqual(cached, reused)
        self.assertEqual(loader.call_count, 2)

    def test_clear_invalidates_model_specific_entries(self) -> None:
        cache = MetadataCache()
        partner_loader = Mock(
            side_effect=[
                {"name": {"type": "char"}},
                {"name": {"type": "char", "string": "Name"}},
            ]
        )
        user_loader = Mock(return_value={"login": {"type": "char"}})

        cache.get_or_load("res.partner", loader=partner_loader)
        cache.get_or_load("res.users", loader=user_loader)
        cache.clear(model_name="res.partner")
        partner = cache.get_or_load("res.partner", loader=partner_loader)
        user = cache.get_or_load("res.users", loader=user_loader)

        self.assertEqual(partner, {"name": {"type": "char", "string": "Name"}})
        self.assertEqual(user, {"login": {"type": "char"}})
        self.assertEqual(partner_loader.call_count, 2)
        user_loader.assert_called_once_with()

    def test_clear_without_model_name_removes_all_entries(self) -> None:
        cache = MetadataCache()
        partner_loader = Mock(
            side_effect=[
                {"name": {"type": "char"}},
                {"name": {"type": "char", "string": "Name"}},
            ]
        )
        user_loader = Mock(
            side_effect=[
                {"login": {"type": "char"}},
                {"login": {"type": "char", "string": "Login"}},
            ]
        )

        cache.get_or_load("res.partner", loader=partner_loader)
        cache.get_or_load("res.users", loader=user_loader)
        self.assertEqual(len(cache), 2)

        cache.clear()

        partner = cache.get_or_load("res.partner", loader=partner_loader)
        user = cache.get_or_load("res.users", loader=user_loader)

        self.assertEqual(partner, {"name": {"type": "char", "string": "Name"}})
        self.assertEqual(user, {"login": {"type": "char", "string": "Login"}})
        self.assertEqual(len(cache), 2)

    def test_returns_defensive_copies_for_cached_metadata(self) -> None:
        cache = MetadataCache()
        loader = Mock(return_value={"name": {"type": "char"}})

        first = cache.get_or_load("res.partner", loader=loader)
        first["name"]["type"] = "integer"
        second = cache.get_or_load("res.partner", loader=loader)

        self.assertEqual(second, {"name": {"type": "char"}})
        loader.assert_called_once_with()