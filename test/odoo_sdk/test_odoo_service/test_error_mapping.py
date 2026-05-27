import unittest
import xmlrpc.client

from odoo_sdk.odoo_service import (
    OdooAuthenticationError,
    OdooServerError,
    OdooTransportError,
    OdooValidationError,
)
from odoo_sdk.odoo_service.errors import (
    map_authentication_failure,
    map_authentication_fault,
    map_fault,
    map_transport_error,
)

class TestErrorMapping(unittest.TestCase):
    def test_map_authentication_failure_sets_auth_operation(self) -> None:
        error = map_authentication_failure(detail="invalid credentials")

        self.assertIsInstance(error, OdooAuthenticationError)
        self.assertEqual(str(error), "Odoo authentication failed")
        self.assertEqual(error.operation, "authenticate")
        self.assertEqual(error.detail, "invalid credentials")

    def test_map_authentication_fault_preserves_fault_context(self) -> None:
        fault = xmlrpc.client.Fault(
            7,
            "  odoo.exceptions.AccessDenied:\n bad login or password  ",
        )

        error = map_authentication_fault(fault)

        self.assertIsInstance(error, OdooAuthenticationError)
        self.assertEqual(error.fault_code, 7)
        self.assertEqual(
            error.fault_string,
            "odoo.exceptions.AccessDenied: bad login or password",
        )
        self.assertEqual(error.detail, error.fault_string)

    def test_map_fault_classifies_access_denied_as_authentication_error(
        self,
    ) -> None:
        fault = xmlrpc.client.Fault(8, "odoo.exceptions.AccessDenied: login failed")

        error = map_fault(fault, model="res.users", method="search")

        self.assertIsInstance(error, OdooAuthenticationError)
        self.assertEqual(error.operation, "res.users.search")

    def test_map_fault_classifies_user_error_as_validation_error(self) -> None:
        fault = xmlrpc.client.Fault(
            9,
            "odoo.exceptions.UserError: constraint violated",
        )

        error = map_fault(fault, model="res.partner", method="write")

        self.assertIsInstance(error, OdooValidationError)
        self.assertEqual(error.operation, "res.partner.write")

    def test_map_fault_falls_back_to_server_error(self) -> None:
        fault = xmlrpc.client.Fault(10, "unexpected crash")

        error = map_fault(fault, model="res.partner", method="unlink")

        self.assertIsInstance(error, OdooServerError)
        self.assertEqual(str(error), "Odoo server error (res.partner.unlink)")

    def test_map_transport_error_defaults_to_execute_operation(self) -> None:
        error = map_transport_error(OSError("  network\n down  "))

        self.assertIsInstance(error, OdooTransportError)
        self.assertEqual(str(error), "Odoo transport error (execute)")
        self.assertEqual(error.operation, "execute")
        self.assertEqual(error.detail, "network down")
