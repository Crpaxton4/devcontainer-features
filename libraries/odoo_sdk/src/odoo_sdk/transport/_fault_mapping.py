from __future__ import annotations

import xmlrpc.client
from typing import Optional

from ._http_error_mapping import _NAME_MAP
from .errors import OdooError, OdooServerError


def _message_after_marker(fault_string: str, marker: str) -> str:
    """Extract the human-readable message that trails an exception marker.

    This helper is necessary because Odoo XML-RPC faults carry the full server-side
    traceback in ``faultString``, with the actionable message appearing after the
    ``odoo.exceptions.<Name>:`` marker on the final line; callers want that message,
    not the surrounding traceback.

    :param fault_string: Raw ``faultString`` from the XML-RPC fault.
    :type fault_string: str
    :param marker: Exception marker known to be present in ``fault_string``.
    :type marker: str
    :return: The trimmed message following the last occurrence of ``marker``, with a
        leading ``:`` separator removed. Empty when nothing follows the marker.
    :rtype: str
    """
    remainder = fault_string.rsplit(marker, 1)[-1].strip()
    if remainder.startswith(":"):
        return remainder[1:].strip()
    return remainder


def map_xmlrpc_fault(
    fault: xmlrpc.client.Fault,
    *,
    model: Optional[str] = None,
    method: Optional[str] = None,
) -> OdooError:
    """Map an :class:`xmlrpc.client.Fault` to an SDK exception.

    This function is necessary because the default XML-RPC transport must classify
    server-side faults into the same :class:`OdooError` taxonomy the JSON-2 transport
    already exposes, so that ``OdooError`` subclasses are catchable on the primary
    path and ``fault_code`` / ``fault_string`` stop being dead fields. The shared
    ``odoo.exceptions.*`` name-to-class map from :mod:`._http_error_mapping` is scanned
    against ``faultString`` so both transports agree on classification.

    :param fault: XML-RPC fault raised by the server.
    :type fault: xmlrpc.client.Fault
    :param model: Odoo model name involved in the call, defaults to None.
    :type model: Optional[str]
    :param method: Odoo method name involved in the call, defaults to None.
    :type method: Optional[str]
    :return: An SDK exception instance appropriate for the fault, carrying the raw
        ``fault_code`` and ``fault_string``.
    :rtype: OdooError
    """
    fault_string = fault.faultString or ""
    fault_code = fault.faultCode

    error_class: type[OdooError] = OdooServerError
    message = fault_string
    for marker, mapped_class in _NAME_MAP.items():
        if marker in fault_string:
            error_class = mapped_class
            message = _message_after_marker(fault_string, marker)
            break

    if not message:
        message = f"XML-RPC fault {fault_code}"

    return error_class(
        message,
        model=model,
        method=method,
        fault_code=fault_code,
        fault_string=fault_string,
    )
