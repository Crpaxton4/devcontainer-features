"""Tests for the SDK capability guard (issue #642).

``assert_sdk_configured`` replaced the old Odoo-devcontainer environment guard,
which asserted three runtime markers (``ODOO_VERSION``, ``/etc/odoo/odoo.conf``,
``/mnt/extra-addons``) that no code path in this package reads. These tests pin
the two preconditions the tracker commands genuinely have, and that neither is
checked by building an :class:`OdooClient`.
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from odoo_sdk.state import LocalConfig, TrackerStateMissingError
from odoo_sdk.utilities.env import assert_sdk_configured

_MOD = "odoo_sdk.utilities.env"


class TestAssertSdkConfigured(unittest.TestCase):
    def _patch_config(self, ok: bool = True):
        """Patch ``LocalConfig.load`` so settings resolution passes or fails."""
        config = MagicMock(spec=LocalConfig)
        if not ok:
            config.connection_settings.side_effect = ValueError(
                "Missing Odoo connection settings: db, url. Configure them with "
                "environment variables, the config file, or override them with "
                "constructor arguments."
            )
        return patch(f"{_MOD}.LocalConfig.load", return_value=config)

    def test_passes_when_both_preconditions_met(self):
        with self._patch_config(), patch(f"{_MOD}.assert_tracker_db_present"):
            self.assertIsNone(assert_sdk_configured())

    def test_raises_value_error_when_connection_settings_missing(self):
        with self._patch_config(ok=False), patch(f"{_MOD}.assert_tracker_db_present"):
            with self.assertRaises(ValueError) as ctx:
                assert_sdk_configured()
        self.assertIn("Missing Odoo connection settings", str(ctx.exception))

    def test_raises_tracker_state_missing_when_db_absent(self):
        with self._patch_config(), patch(
            f"{_MOD}.assert_tracker_db_present",
            side_effect=TrackerStateMissingError("no tracker database at /nope"),
        ):
            with self.assertRaises(TrackerStateMissingError):
                assert_sdk_configured()

    def test_connection_settings_checked_before_tracker_db(self):
        """Settings resolve first, so the cheaper/more common failure wins."""
        db_guard = MagicMock()
        with self._patch_config(ok=False), patch(
            f"{_MOD}.assert_tracker_db_present", db_guard
        ):
            with self.assertRaises(ValueError):
                assert_sdk_configured()
        db_guard.assert_not_called()

    def test_ignores_odoo_devcontainer_markers(self):
        """The three markers the old guard demanded are no longer consulted (#642).

        With none of them present but both real preconditions met, the guard must
        pass — this is exactly the "fully provisioned non-Odoo container" that
        #642 reported as wrongly refused.
        """
        with patch.dict("os.environ", {}, clear=True), self._patch_config(), patch(
            f"{_MOD}.assert_tracker_db_present"
        ):
            assert_sdk_configured()  # must not raise

    def test_builds_no_odoo_client(self):
        """The lazy-client contract: settings are resolved, no client constructed."""
        with self._patch_config() as load, patch(f"{_MOD}.assert_tracker_db_present"):
            assert_sdk_configured()
        load.return_value.connection_settings.assert_called_once_with()

    def test_real_config_and_db_paths_are_wired(self):
        """End-to-end over the real helpers, with only the filesystem faked."""
        with patch.dict(
            "os.environ",
            {
                "ODOO_URL": "https://example.odoo.com",
                "ODOO_DB": "example",
                "ODOO_USERNAME": "bot",
                "ODOO_PASSWORD": "secret",
                "ODOO_SDK_CONFIG": "/nonexistent/odoo-sdk.toml",
            },
            clear=True,
        ), patch(
            "odoo_sdk.state.db.tracker_db_path", return_value=Path("/nonexistent/t.db")
        ):
            with self.assertRaises(TrackerStateMissingError) as ctx:
                assert_sdk_configured()
        self.assertIn("setup.sh", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
