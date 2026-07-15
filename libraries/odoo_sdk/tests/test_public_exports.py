import importlib
import unittest


class TestRecordsetFirstPublicExports(unittest.TestCase):
    def test_top_level_all_exposes_recordset_first_exports(self) -> None:
        package = importlib.import_module("odoo_sdk")

        expected_exports = {
            "commands",
            "Command",
            "OdooClient",
            "OdooConnectionSettings",
            "OdooExecutor",
            "OdooJson2Executor",
            "OdooMCPServer",
            "OdooRecordset",
            "OdooRpcExecutor",
            "Registry",
            "Domain",
            "DomainExpression",
            "Record",
            "OdooError",
            "OdooAuthenticationError",
            "OdooAccessError",
            "OdooValidationError",
            "OdooMissingRecordError",
            "OdooTransportError",
            "OdooServerError",
            "DeletionNotSupportedError",
        }

        self.assertEqual(set(package.__all__), expected_exports)

    def test_top_level_all_promotes_recordset_primitives(self) -> None:
        package = importlib.import_module("odoo_sdk")

        self.assertIn("DomainExpression", package.__all__)
        self.assertIn("OdooRecordset", package.__all__)
        self.assertTrue(hasattr(package, "DomainExpression"))
        self.assertTrue(hasattr(package, "OdooRecordset"))

    def test_top_level_all_promotes_command_and_json2_executor(self) -> None:
        package = importlib.import_module("odoo_sdk")

        self.assertIn("Command", package.__all__)
        self.assertIn("OdooJson2Executor", package.__all__)
        self.assertTrue(hasattr(package, "Command"))
        self.assertTrue(hasattr(package, "OdooJson2Executor"))

    def test_mcp_server_resolves_lazily_through_module_getattr(self) -> None:
        package = importlib.import_module("odoo_sdk")
        from odoo_sdk.mcp.server import OdooMCPServer

        self.assertIn("OdooMCPServer", package.__all__)
        self.assertIs(package.OdooMCPServer, OdooMCPServer)

    def test_unknown_attribute_raises_attribute_error(self) -> None:
        package = importlib.import_module("odoo_sdk")

        with self.assertRaises(AttributeError):
            package.NotAPublicSymbol

    def test_top_level_all_exposes_error_taxonomy(self) -> None:
        package = importlib.import_module("odoo_sdk")
        errors = importlib.import_module("odoo_sdk.transport.errors")

        taxonomy = [
            "OdooError",
            "OdooAuthenticationError",
            "OdooAccessError",
            "OdooValidationError",
            "OdooMissingRecordError",
            "OdooTransportError",
            "OdooServerError",
            "DeletionNotSupportedError",
        ]
        for name in taxonomy:
            self.assertIn(name, package.__all__)
            self.assertTrue(hasattr(package, name))
            # The top-level re-export is the same object as the canonical
            # transport.errors definition, not a shadowing duplicate.
            self.assertIs(getattr(package, name), getattr(errors, name))
