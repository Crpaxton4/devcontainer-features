import importlib
import unittest
import warnings

import odoo_sdk.fields as fields_pkg
import odoo_sdk.fields.commands as commands_mod
from odoo_sdk.fields.commands import X2ManyCommand


class TestDeprecatedCommandAlias(unittest.TestCase):
    """Verify the renamed x2many builder still resolves under its old name."""

    def test_module_alias_returns_x2many_command_with_warning(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            alias = commands_mod.Command
        self.assertIs(alias, X2ManyCommand)
        self.assertEqual(len(caught), 1)
        self.assertIs(caught[0].category, DeprecationWarning)
        self.assertIn("X2ManyCommand", str(caught[0].message))

    def test_module_unknown_attribute_raises(self) -> None:
        with self.assertRaises(AttributeError) as ctx:
            commands_mod.DoesNotExist
        self.assertIn("DoesNotExist", str(ctx.exception))

    def test_package_alias_returns_x2many_command(self) -> None:
        self.assertIs(fields_pkg.Command, X2ManyCommand)

    def test_package_unknown_attribute_raises(self) -> None:
        with self.assertRaises(AttributeError) as ctx:
            fields_pkg.DoesNotExist
        self.assertIn("DoesNotExist", str(ctx.exception))

    def test_registry_command_is_distinct_class(self) -> None:
        registry_command = importlib.import_module(
            "odoo_sdk.commands.command"
        ).Command
        self.assertIsNot(registry_command, X2ManyCommand)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
