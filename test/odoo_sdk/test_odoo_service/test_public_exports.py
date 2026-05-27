import importlib
import unittest


class TestPhaseAPublicExports(unittest.TestCase):
    def test_top_level_all_preserves_supported_exports(self) -> None:
        package = importlib.import_module("src")

        expected_exports = {
            "command_registry",
            "odoo_service",
            "OdooClient",
            "OdooConnectionSettings",
            "OdooExecutor",
            "OdooModel",
            "OdooQuery",
            "OdooRpcExecutor",
            "CommandDispatcher",
            "Domain",
            "Record",
            "__version__",
        }

        self.assertEqual(set(package.__all__), expected_exports)

    def test_top_level_all_excludes_phase_a_internal_primitives(self) -> None:
        package = importlib.import_module("src")

        self.assertNotIn("OdooEnv", package.__all__)
        self.assertNotIn("DomainExpression", package.__all__)
        self.assertNotIn("OdooRecordset", package.__all__)
        self.assertFalse(hasattr(package, "OdooEnv"))
        self.assertFalse(hasattr(package, "DomainExpression"))
        self.assertFalse(hasattr(package, "OdooRecordset"))

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

    def test_service_all_excludes_phase_a_internal_primitives(self) -> None:
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
                "OdooExecutor",
                "OdooModel",
                "OdooQuery",
                "OdooRpcExecutor",
                "X2ManyCommand",
            },
        )
        self.assertTrue(hasattr(service_package, "X2ManyCommand"))
        self.assertFalse(hasattr(service_package, "OdooEnv"))
        self.assertFalse(hasattr(service_package, "DomainExpression"))
        self.assertFalse(hasattr(service_package, "OdooRecordset"))
