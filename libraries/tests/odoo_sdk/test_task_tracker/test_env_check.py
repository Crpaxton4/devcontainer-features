import unittest
from unittest.mock import patch

from odoo_sdk.task_tracker.env_check import (
    OdooDevcontainerRequiredError,
    assert_odoo_devcontainer,
)


class TestAssertOdooDevcontainer(unittest.TestCase):
    def _patch_all_present(self):
        """Patch environment so all three checks pass."""
        return (
            patch.dict("os.environ", {"ODOO_VERSION": "17.0"}),
            patch("odoo_sdk.task_tracker.env_check.Path.exists", return_value=True),
        )

    def test_passes_when_all_conditions_met(self):
        with (
            patch.dict("os.environ", {"ODOO_VERSION": "17.0"}),
            patch("odoo_sdk.task_tracker.env_check.Path.exists", return_value=True),
        ):
            assert_odoo_devcontainer()  # must not raise

    def test_raises_when_odoo_version_missing(self):
        env = {"HOME": "/home/user"}  # deliberately no ODOO_VERSION
        with (
            patch.dict("os.environ", env, clear=True),
            patch("odoo_sdk.task_tracker.env_check.Path.exists", return_value=True),
        ):
            with self.assertRaises(OdooDevcontainerRequiredError) as ctx:
                assert_odoo_devcontainer()
        self.assertIn("ODOO_VERSION", str(ctx.exception))

    def test_raises_when_odoo_conf_missing(self):
        def exists_side_effect(self_path):
            return str(self_path) != "/etc/odoo/odoo.conf"

        with (
            patch.dict("os.environ", {"ODOO_VERSION": "17.0"}),
            patch(
                "odoo_sdk.task_tracker.env_check.Path.exists",
                autospec=True,
                side_effect=exists_side_effect,
            ),
        ):
            with self.assertRaises(OdooDevcontainerRequiredError) as ctx:
                assert_odoo_devcontainer()
        self.assertIn("odoo.conf", str(ctx.exception))

    def test_raises_when_extra_addons_missing(self):
        def exists_side_effect(self_path):
            return str(self_path) != "/mnt/extra-addons"

        with (
            patch.dict("os.environ", {"ODOO_VERSION": "17.0"}),
            patch(
                "odoo_sdk.task_tracker.env_check.Path.exists",
                autospec=True,
                side_effect=exists_side_effect,
            ),
        ):
            with self.assertRaises(OdooDevcontainerRequiredError) as ctx:
                assert_odoo_devcontainer()
        self.assertIn("extra-addons", str(ctx.exception))

    def test_error_is_runtime_error_subclass(self):
        self.assertTrue(issubclass(OdooDevcontainerRequiredError, RuntimeError))


if __name__ == "__main__":
    unittest.main()
