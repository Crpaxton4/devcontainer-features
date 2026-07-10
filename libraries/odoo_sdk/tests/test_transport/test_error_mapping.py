import unittest
import xmlrpc.client
from typing import Optional, Type

from odoo_sdk.transport.errors import (
    OdooAccessError,
    OdooAuthenticationError,
    OdooError,
    OdooMissingRecordError,
    OdooServerError,
    OdooTransportError,
    OdooValidationError,
)

_FAULT_MARKERS: tuple[tuple[Type[OdooError], tuple[str, ...]], ...] = (
    (
        OdooMissingRecordError,
        (
            "odoo.exceptions.missingerror",
            "missingerror:",
            "record does not exist",
            "does not exist or has been deleted",
            "has already been deleted",
        ),
    ),
    (
        OdooAuthenticationError,
        (
            "odoo.exceptions.accessdenied",
            "accessdenied",
            "bad login or password",
            "authentication failed",
            "login failed",
        ),
    ),
    (
        OdooAccessError,
        (
            "odoo.exceptions.accesserror",
            "accesserror:",
            "access denied",
            "permission denied",
            "operation not allowed",
        ),
    ),
    (
        OdooValidationError,
        (
            "odoo.exceptions.validationerror",
            "validationerror:",
            "odoo.exceptions.usererror",
            "usererror:",
            "wrong value",
            "constraint",
        ),
    ),
)


def _normalize_detail(value: str) -> str:
    return " ".join(value.split())


def _operation_name(
    *,
    model: Optional[str] = None,
    method: Optional[str] = None,
    operation: Optional[str] = None,
) -> str:
    if model is not None and method is not None:
        return f"{model}.{method}"
    if operation is not None:
        return operation
    return "execute"


def _message_for_error(
    error_type: Type[OdooError],
    *,
    operation_name: str,
) -> str:
    prefix = {
        OdooAuthenticationError: "Odoo authentication failed",
        OdooAccessError: "Odoo access denied",
        OdooValidationError: "Odoo validation failed",
        OdooMissingRecordError: "Odoo record was not found",
        OdooTransportError: "Odoo transport error",
        OdooServerError: "Odoo server error",
    }.get(error_type, "Odoo error")

    if operation_name == "authenticate":
        if error_type is OdooTransportError:
            return f"{prefix} during authenticate"
        return prefix
    return f"{prefix} ({operation_name})"


def _classify_fault(fault_string: str) -> Type[OdooError]:
    normalized = _normalize_detail(fault_string).casefold()
    for error_type, markers in _FAULT_MARKERS:
        if any(marker in normalized for marker in markers):
            return error_type
    return OdooServerError


def map_authentication_failure(
    *, detail: Optional[str] = None
) -> OdooAuthenticationError:
    return OdooAuthenticationError(
        _message_for_error(OdooAuthenticationError, operation_name="authenticate"),
        operation="authenticate",
        detail=detail,
    )


def map_authentication_fault(
    fault: xmlrpc.client.Fault,
) -> OdooAuthenticationError:
    fault_string = _normalize_detail(fault.faultString)
    return OdooAuthenticationError(
        _message_for_error(OdooAuthenticationError, operation_name="authenticate"),
        operation="authenticate",
        fault_code=fault.faultCode,
        fault_string=fault_string,
        detail=fault_string,
    )


def map_transport_error(
    exc: BaseException,
    *,
    model: Optional[str] = None,
    method: Optional[str] = None,
    operation: Optional[str] = None,
    detail: Optional[str] = None,
) -> OdooTransportError:
    operation_name = _operation_name(model=model, method=method, operation=operation)
    resolved_detail = detail if detail is not None else _normalize_detail(str(exc))
    return OdooTransportError(
        _message_for_error(OdooTransportError, operation_name=operation_name),
        operation=operation_name,
        model=model,
        method=method,
        detail=resolved_detail,
    )


def map_fault(
    fault: xmlrpc.client.Fault,
    *,
    model: str,
    method: str,
) -> OdooError:
    operation_name = _operation_name(model=model, method=method)
    fault_string = _normalize_detail(fault.faultString)
    error_type = _classify_fault(fault_string)
    return error_type(
        _message_for_error(error_type, operation_name=operation_name),
        operation=operation_name,
        model=model,
        method=method,
        fault_code=fault.faultCode,
        fault_string=fault_string,
        detail=fault_string,
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

        error = map_fault(fault, model="res.partner", method="write")

        self.assertIsInstance(error, OdooServerError)
        self.assertEqual(str(error), "Odoo server error (res.partner.write)")

    def test_map_transport_error_defaults_to_execute_operation(self) -> None:
        error = map_transport_error(OSError("  network\n down  "))

        self.assertIsInstance(error, OdooTransportError)
        self.assertEqual(str(error), "Odoo transport error (execute)")
        self.assertEqual(error.operation, "execute")
        self.assertEqual(error.detail, "network down")
