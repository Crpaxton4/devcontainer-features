from __future__ import annotations

import xmlrpc.client
from typing import Optional, Type

from .errors import (
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
    """Collapse fault detail text into a normalized single-line string.

    This helper is necessary because error classification and message formatting need
    stable text that is not sensitive to Odoo's multiline traceback formatting.

    :param value: Raw detail or fault string from Odoo.
    :type value: str
    :return: Whitespace-normalized detail text.
    :rtype: str
    """
    return " ".join(value.split())


def _operation_name(
    *,
    model: Optional[str] = None,
    method: Optional[str] = None,
    operation: Optional[str] = None,
) -> str:
    """Resolve a human-readable operation name for mapped errors.

    This helper is necessary because some failures originate from model-method calls
    while others occur during higher-level operations such as authentication.

    :param model: Model involved in the failing call, defaults to None.
    :type model: Optional[str]
    :param method: Method involved in the failing call, defaults to None.
    :type method: Optional[str]
    :param operation: Explicit logical operation name, defaults to None.
    :type operation: Optional[str]
    :return: Operation label used in exception metadata and messages.
    :rtype: str
    """
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
    """Build the public-facing message for a classified error type.

    This helper is necessary because the SDK wants consistent messages across mapped
    failures without duplicating formatting rules in every mapper function.

    :param error_type: Classified SDK exception type.
    :type error_type: Type[OdooError]
    :param operation_name: Logical operation being described.
    :type operation_name: str
    :return: Human-readable error message.
    :rtype: str
    """
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
    """Classify a normalized XML-RPC fault string into an SDK error type.

    This helper is necessary because Odoo faults often communicate their semantics in
    traceback text rather than stable machine-readable codes.

    :param fault_string: Normalized XML-RPC fault string.
    :type fault_string: str
    :return: Most specific matching SDK error subtype.
    :rtype: Type[OdooError]
    """
    normalized = _normalize_detail(fault_string).casefold()
    for error_type, markers in _FAULT_MARKERS:
        if any(marker in normalized for marker in markers):
            return error_type
    return OdooServerError


def map_authentication_failure(*, detail: Optional[str] = None) -> OdooAuthenticationError:
    """Create an authentication error for non-fault login failures.

    This helper is necessary because authentication can fail without an XML-RPC fault
    object, but callers still need a classified SDK exception.

    :param detail: Optional diagnostic detail about the failure, defaults to None.
    :type detail: Optional[str]
    :return: Authentication error carrying the normalized operation metadata.
    :rtype: OdooAuthenticationError
    """
    return OdooAuthenticationError(
        _message_for_error(OdooAuthenticationError, operation_name="authenticate"),
        operation="authenticate",
        detail=detail,
    )


def map_authentication_fault(
    fault: xmlrpc.client.Fault,
) -> OdooAuthenticationError:
    """Map an XML-RPC authentication fault into an SDK exception.

    This helper is necessary because authentication faults have a fixed logical
    operation name and should always classify as authentication failures.

    :param fault: XML-RPC fault returned during authentication.
    :type fault: xmlrpc.client.Fault
    :return: Authentication error populated with fault metadata.
    :rtype: OdooAuthenticationError
    """
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
    """Map a local transport exception into an SDK transport error.

    This helper is necessary because network and protocol failures do not arrive as
    XML-RPC faults but still need structured SDK classification and operation labels.

    :param exc: Underlying transport exception.
    :type exc: BaseException
    :param model: Model involved in the failing call, defaults to None.
    :type model: Optional[str]
    :param method: Method involved in the failing call, defaults to None.
    :type method: Optional[str]
    :param operation: Explicit logical operation name, defaults to None.
    :type operation: Optional[str]
    :param detail: Optional diagnostic detail override, defaults to None.
    :type detail: Optional[str]
    :return: Classified transport error.
    :rtype: OdooTransportError
    """
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
    """Map an XML-RPC model fault into the most specific SDK exception.

    This helper is necessary because the executor needs one central classification path
    that preserves model, method, and raw fault diagnostics on every raised error.

    :param fault: XML-RPC fault returned by Odoo.
    :type fault: xmlrpc.client.Fault
    :param model: Model involved in the failing call.
    :type model: str
    :param method: Method involved in the failing call.
    :type method: str
    :return: Classified SDK exception populated with fault metadata.
    :rtype: OdooError
    """
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