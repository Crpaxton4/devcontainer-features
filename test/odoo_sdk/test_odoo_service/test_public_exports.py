import importlib
import unittest


class TestRecordsetFirstPublicExports(unittest.TestCase):
    def test_top_level_all_exposes_recordset_first_exports(self) -> None:
        package = importlib.import_module("src")

        expected_exports = {
            "command_registry",
            "odoo_service",
            "OdooClient",
            "OdooConnectionSettings",
            "OdooEnv",
            "OdooExecutor",
            "OdooModel",
            "OdooQuery",
            "OdooRecordset",
            "OdooRpcExecutor",
            "CommandDispatcher",
            "Domain",
            "DomainExpression",
            "Record",
            "__version__",
        }

        self.assertEqual(set(package.__all__), expected_exports)

    def test_top_level_all_promotes_recordset_primitives(self) -> None:
        package = importlib.import_module("src")

        self.assertIn("OdooEnv", package.__all__)
        self.assertIn("DomainExpression", package.__all__)
        self.assertIn("OdooRecordset", package.__all__)
        self.assertTrue(hasattr(package, "OdooEnv"))
        self.assertTrue(hasattr(package, "DomainExpression"))
        self.assertTrue(hasattr(package, "OdooRecordset"))

    def test_top_level_all_excludes_phase_b_error_taxonomy(self) -> None:
        package = importlib.import_module("src")

        self.assertNotIn("OdooError", package.__all__)
        self.assertNotIn("OdooAuthenticationError", package.__all__)
        self.assertNotIn("OdooAccessError", package.__all__)
        self.assertNotIn("OdooValidationError", package.__all__)
        self.assertNotIn("OdooMissingRecordError", package.__all__)
        self.assertNotIn("OdooTransportError", package.__all__)
        self.assertNotIn("OdooServerError", package.__all__)
        self.assertFalse(hasattr(package, "OdooError"))

    def test_service_all_exposes_recordset_first_primitives(self) -> None:
        service_package = importlib.import_module("odoo_sdk.odoo_service")

        self.assertEqual(
            set(service_package.__all__),
            {
                "OdooClient",
                "OdooConnectionSettings",
                "OdooError",
                "OdooAuthenticationError",
                "OdooAccessError",
                "OdooValidationError",
                "OdooMissingRecordError",
                "OdooTransportError",
                "OdooServerError",
                "OdooEnv",
                "OdooExecutor",
                "OdooModel",
                "OdooQuery",
                "OdooRecordset",
                "OdooRpcExecutor",
                "DomainExpression",
                "X2ManyCommand",
            },
        )
        self.assertTrue(hasattr(service_package, "X2ManyCommand"))
        self.assertTrue(hasattr(service_package, "OdooEnv"))
        self.assertTrue(hasattr(service_package, "DomainExpression"))
        self.assertTrue(hasattr(service_package, "OdooRecordset"))
