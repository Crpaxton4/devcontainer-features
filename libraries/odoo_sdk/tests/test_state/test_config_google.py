"""Tests for the Google-ingestion behavior settings on LocalConfig (issue #370)."""

import unittest

from odoo_sdk.state import LocalConfig


class TestCalendarTickMins(unittest.TestCase):
    def test_default(self):
        self.assertEqual(LocalConfig().calendar_tick_mins, 5)

    def test_file_value_coerced(self):
        self.assertEqual(LocalConfig(behavior={"calendar_tick_mins": "10"}).calendar_tick_mins, 10)

    def test_invalid_and_non_positive_fall_back(self):
        self.assertEqual(LocalConfig(behavior={"calendar_tick_mins": "x"}).calendar_tick_mins, 5)
        self.assertEqual(LocalConfig(behavior={"calendar_tick_mins": 0}).calendar_tick_mins, 5)
        self.assertEqual(LocalConfig(behavior={"calendar_tick_mins": -3}).calendar_tick_mins, 5)


class TestIngestSubjects(unittest.TestCase):
    def test_default_enabled(self):
        self.assertTrue(LocalConfig().ingest_subjects)

    def test_bool_and_string_forms(self):
        self.assertTrue(LocalConfig(behavior={"ingest_subjects": True}).ingest_subjects)
        self.assertTrue(LocalConfig(behavior={"ingest_subjects": "yes"}).ingest_subjects)
        self.assertFalse(LocalConfig(behavior={"ingest_subjects": False}).ingest_subjects)
        self.assertFalse(LocalConfig(behavior={"ingest_subjects": "0"}).ingest_subjects)
        self.assertFalse(LocalConfig(behavior={"ingest_subjects": "off"}).ingest_subjects)


class TestGoogleSyncWindowDays(unittest.TestCase):
    def test_default(self):
        self.assertEqual(LocalConfig().google_sync_window_days, 30)

    def test_coercion_and_fallback(self):
        self.assertEqual(LocalConfig(behavior={"google_sync_window_days": "7"}).google_sync_window_days, 7)
        self.assertEqual(LocalConfig(behavior={"google_sync_window_days": "bad"}).google_sync_window_days, 30)
        self.assertEqual(LocalConfig(behavior={"google_sync_window_days": -1}).google_sync_window_days, 30)


class TestGoogleTokenPath(unittest.TestCase):
    def test_default_none(self):
        self.assertIsNone(LocalConfig().google_token_path)

    def test_explicit_value(self):
        self.assertEqual(
            LocalConfig(behavior={"google_token_path": "/tmp/tok.json"}).google_token_path,
            "/tmp/tok.json",
        )

    def test_empty_string_is_none(self):
        self.assertIsNone(LocalConfig(behavior={"google_token_path": ""}).google_token_path)


if __name__ == "__main__":
    unittest.main()
