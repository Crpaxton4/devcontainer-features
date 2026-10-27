"""Tests for the SDK capability guard (issue #642).

``assert_configured`` replaced the old Odoo-devcontainer environment guard,
which asserted three runtime markers (``ODOO_VERSION``, ``/etc/odoo/odoo.conf``,
``/mnt/extra-addons``) that no code path in this package reads. These tests pin
the one precondition the commands genuinely have, resolvable connection
settings, and that it is not checked by building an :class:`OdooClient`.
"""

import unittest
from unittest.mock import patch

from odoo_cli.bootstrap import assert_configured

_MOD = "odoo_cli.bootstrap"

_FULL_ENV = {
    "ODOO_URL": "https://example.odoo.com",
    "ODOO_DB": "example",
    "ODOO_USERNAME": "bot",
    "ODOO_PASSWORD": "secret",
}


class TestAssertConfigured(unittest.TestCase):
    def test_passes_when_connection_settings_resolve(self):
        with patch.dict("os.environ", _FULL_ENV):
            self.assertIsNone(assert_configured())

    def test_raises_value_error_when_connection_settings_missing(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ValueError) as ctx:
                assert_configured()
        self.assertIn("Missing Odoo connection settings", str(ctx.exception))

    def test_ignores_odoo_devcontainer_markers(self):
        """The three markers the old guard demanded are no longer consulted (#642).

        With none of them present but the settings resolvable, the guard must
        pass. This is the "fully provisioned non-Odoo container" that #642
        reported as wrongly refused.
        """
        with patch.dict("os.environ", _FULL_ENV, clear=True):
            assert_configured()  # must not raise

    def test_builds_no_odoo_client(self):
        """The lazy-client contract: settings are resolved, no client constructed."""
        with (
            patch.dict("os.environ", _FULL_ENV),
            patch(f"{_MOD}.OdooClient") as client_class,
        ):
            assert_configured()
        client_class.assert_not_called()
        client_class.from_settings.assert_not_called()


if __name__ == "__main__":
    unittest.main()
