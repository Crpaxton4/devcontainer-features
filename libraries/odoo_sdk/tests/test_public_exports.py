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

    def test_top_level_all_excludes_error_taxonomy(self) -> None:
        package = importlib.import_module("odoo_sdk")

        self.assertNotIn("OdooError", package.__all__)
        self.assertNotIn("OdooAuthenticationError", package.__all__)
        self.assertNotIn("OdooAccessError", package.__all__)
        self.assertNotIn("OdooValidationError", package.__all__)
        self.assertNotIn("OdooMissingRecordError", package.__all__)
        self.assertNotIn("OdooTransportError", package.__all__)
        self.assertNotIn("OdooServerError", package.__all__)
        self.assertFalse(hasattr(package, "OdooError"))
