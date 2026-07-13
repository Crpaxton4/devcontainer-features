import unittest
import xmlrpc.client

from odoo_sdk.transport._fault_mapping import map_xmlrpc_fault
from odoo_sdk.transport.errors import (
    OdooAccessError,
    OdooAuthenticationError,
    OdooMissingRecordError,
    OdooServerError,
    OdooValidationError,
)


def _fault(fault_string: str, fault_code: int = 1) -> xmlrpc.client.Fault:
    return xmlrpc.client.Fault(fault_code, fault_string)


def _traceback(marker_line: str) -> str:
    return (
        "Traceback (most recent call last):\n"
        '  File "/odoo/service/model.py", line 62, in dispatch\n'
        "    result = method(*args, **kwargs)\n"
        f"{marker_line}"
    )


class TestMapXmlrpcFault(unittest.TestCase):
    # --- faultString-marker-driven mapping (one per exception class) ---

    def test_access_denied_marker_maps_authentication_error(self) -> None:
        exc = map_xmlrpc_fault(
            _fault("odoo.exceptions.AccessDenied: bad credentials"),
            model="res.users",
            method="login",
        )
        self.assertIsInstance(exc, OdooAuthenticationError)
        self.assertEqual(str(exc), "bad credentials")

    def test_access_error_marker_maps_access_error(self) -> None:
        exc = map_xmlrpc_fault(_fault("odoo.exceptions.AccessError: not allowed"))
        self.assertIsInstance(exc, OdooAccessError)
        self.assertEqual(str(exc), "not allowed")

    def test_missing_error_marker_maps_missing_record_error(self) -> None:
        exc = map_xmlrpc_fault(_fault("odoo.exceptions.MissingError: record gone"))
        self.assertIsInstance(exc, OdooMissingRecordError)
        self.assertEqual(str(exc), "record gone")

    def test_validation_error_marker_maps_validation_error(self) -> None:
        exc = map_xmlrpc_fault(_fault("odoo.exceptions.ValidationError: bad value"))
        self.assertIsInstance(exc, OdooValidationError)
        self.assertEqual(str(exc), "bad value")

    def test_user_error_marker_maps_server_error(self) -> None:
        exc = map_xmlrpc_fault(_fault("odoo.exceptions.UserError: user message"))
        self.assertIsInstance(exc, OdooServerError)
        self.assertEqual(str(exc), "user message")

    # --- fallback classification ---

    def test_unknown_marker_maps_server_error(self) -> None:
        exc = map_xmlrpc_fault(_fault("SomeInternalError: boom"))
        self.assertIsInstance(exc, OdooServerError)
        # No known marker: the raw faultString is used verbatim as the message.
        self.assertEqual(str(exc), "SomeInternalError: boom")

    def test_exact_type_not_a_subclass_for_unknown_marker(self) -> None:
        exc = map_xmlrpc_fault(_fault("plain failure"))
        self.assertIs(type(exc), OdooServerError)

    # --- message extraction from a full server traceback ---

    def test_message_extracted_from_traceback_last_line(self) -> None:
        fault_string = _traceback(
            "odoo.exceptions.AccessError: You are not allowed to access "
            "'Contact' (res.partner) records."
        )
        exc = map_xmlrpc_fault(_fault(fault_string, fault_code=3))
        self.assertIsInstance(exc, OdooAccessError)
        self.assertEqual(
            str(exc),
            "You are not allowed to access 'Contact' (res.partner) records.",
        )
        self.assertEqual(exc.fault_string, fault_string)
        self.assertEqual(exc.fault_code, 3)

    def test_marker_without_colon_uses_trailing_text(self) -> None:
        # Defensive: a marker not followed by the usual ``:`` separator still yields
        # the trailing text as the message.
        exc = map_xmlrpc_fault(_fault("odoo.exceptions.UserError\nBusiness rule broke"))
        self.assertIsInstance(exc, OdooServerError)
        self.assertEqual(str(exc), "Business rule broke")

    def test_marker_with_empty_message_falls_back_to_fault_code(self) -> None:
        exc = map_xmlrpc_fault(_fault("odoo.exceptions.AccessDenied", fault_code=9))
        self.assertIsInstance(exc, OdooAuthenticationError)
        self.assertEqual(str(exc), "XML-RPC fault 9")

    def test_empty_fault_string_falls_back_to_fault_code(self) -> None:
        exc = map_xmlrpc_fault(_fault("", fault_code=7))
        self.assertIsInstance(exc, OdooServerError)
        self.assertEqual(str(exc), "XML-RPC fault 7")

    # --- raw fault fields populated ---

    def test_fault_code_and_string_populated(self) -> None:
        fault_string = "odoo.exceptions.ValidationError: nope"
        exc = map_xmlrpc_fault(_fault(fault_string, fault_code=42))
        self.assertEqual(exc.fault_code, 42)
        self.assertEqual(exc.fault_string, fault_string)

    # --- metadata propagation ---

    def test_model_and_method_stored_on_exception(self) -> None:
        exc = map_xmlrpc_fault(
            _fault("odoo.exceptions.MissingError: gone"),
            model="res.partner",
            method="read",
        )
        self.assertEqual(exc.model, "res.partner")
        self.assertEqual(exc.method, "read")

    def test_model_and_method_default_to_none(self) -> None:
        exc = map_xmlrpc_fault(_fault("odoo.exceptions.UserError: oops"))
        self.assertIsNone(exc.model)
        self.assertIsNone(exc.method)


if __name__ == "__main__":
    unittest.main()
