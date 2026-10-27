"""Tests for the meeting-tick ingestion behavior settings on LocalConfig (issue #370)."""

import unittest

from odoo_client.state import LocalConfig


class TestCalendarTickMins(unittest.TestCase):
    def test_default(self):
        self.assertEqual(LocalConfig().calendar_tick_mins, 5)

    def test_file_value_coerced(self):
        self.assertEqual(
            LocalConfig(behavior={"calendar_tick_mins": "10"}).calendar_tick_mins, 10
        )

    def test_invalid_and_non_positive_fall_back(self):
        self.assertEqual(
            LocalConfig(behavior={"calendar_tick_mins": "x"}).calendar_tick_mins, 5
        )
        self.assertEqual(
            LocalConfig(behavior={"calendar_tick_mins": 0}).calendar_tick_mins, 5
        )
        self.assertEqual(
            LocalConfig(behavior={"calendar_tick_mins": -3}).calendar_tick_mins, 5
        )


class TestIngestSubjects(unittest.TestCase):
    def test_default_enabled(self):
        self.assertTrue(LocalConfig().ingest_subjects)

    def test_bool_and_string_forms(self):
        self.assertTrue(LocalConfig(behavior={"ingest_subjects": True}).ingest_subjects)
        self.assertTrue(
            LocalConfig(behavior={"ingest_subjects": "yes"}).ingest_subjects
        )
        self.assertFalse(
            LocalConfig(behavior={"ingest_subjects": False}).ingest_subjects
        )
        self.assertFalse(LocalConfig(behavior={"ingest_subjects": "0"}).ingest_subjects)
        self.assertFalse(
            LocalConfig(behavior={"ingest_subjects": "off"}).ingest_subjects
        )


class TestGoogleSettingsAreGone(unittest.TestCase):
    def test_google_properties_are_removed(self):
        config = LocalConfig()
        self.assertFalse(hasattr(config, "google_sync_window_days"))
        self.assertFalse(hasattr(config, "google_token_path"))


if __name__ == "__main__":
    unittest.main()
