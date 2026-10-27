import importlib
import unittest

from odoo_client.fields.commands import X2ManyCommand


class TestRegistryCommandIsDistinct(unittest.TestCase):
    def test_registry_command_is_distinct_class(self) -> None:
        registry_command = importlib.import_module(
            "odoo_dev_core.commands.command"
        ).Command
        self.assertIsNot(registry_command, X2ManyCommand)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
