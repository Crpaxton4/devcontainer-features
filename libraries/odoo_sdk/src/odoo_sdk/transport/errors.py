from __future__ import annotations

from typing import Optional


class OdooError(RuntimeError):
    """Represent a classified failure that occurred while talking to Odoo.

    The base error is necessary because callers need one SDK-specific exception root
    that preserves operation metadata and can be specialized into more actionable
    subclasses by the error-mapping layer.
    """

    def __init__(
        self,
        message: str,
        *,
        operation: Optional[str] = None,
        model: Optional[str] = None,
        method: Optional[str] = None,
        fault_code: Optional[int] = None,
        fault_string: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> None:
        """Store the message plus structured metadata about an Odoo-facing failure."""
        super().__init__(message)
        self.operation = operation
        self.model = model
        self.method = method
        self.fault_code = fault_code
        self.fault_string = fault_string
        self.detail = detail


class OdooAuthenticationError(OdooError):
    """Raised when credentials or session bootstrap fail."""


class OdooAccessError(OdooError):
    """Raised when Odoo denies access to an otherwise valid operation."""


class OdooValidationError(OdooError):
    """Raised when Odoo rejects supplied values or arguments as invalid."""


class OdooMissingRecordError(OdooError):
    """Raised when an operation targets records that no longer exist."""


class OdooTransportError(OdooError):
    """Raised for client-side transport, protocol, or connectivity failures."""


class OdooServerError(OdooError):
    """Raised for unexpected or unmapped server-side XML-RPC faults."""


class DeletionNotSupportedError(OdooError):
    """Raised when any caller attempts an explicit Odoo record ``unlink``.

    Record deletion is *purposefully* not implemented in this SDK: an on-demand
    ``unlink`` risks irrecoverable data loss, so it is forbidden system-wide and
    must never be implemented. This is intentional, permanent idiot-proofing —
    a correct implementation never reaches this guard. If it fires, that is a
    bug in the caller and the program must fail hard, loudly, and immediately.

    ORM-internal cascade deletes (triggered server-side by ``write`` / x2many
    ``(2, id)`` / ``(3, id)`` commands) are unaffected; only an explicit
    ``unlink`` method call is blocked.
    """


_FORBIDDEN_DELETION_MESSAGE = (
    "Record deletion via 'unlink' is purposefully not implemented for safety. "
    "This operation is permanently disallowed and must never be implemented."
)


def forbid_unlink(method: str) -> None:
    """Raise :class:`DeletionNotSupportedError` when ``method`` is ``unlink``.

    The single shared guard invoked from :func:`odoo_sdk.transport.executor.guarded_execute`,
    the one transport chokepoint that both :meth:`OdooClient.execute` and
    :meth:`OdooRecordset._execute` cross, so every path to an explicit record
    delete — including recordset-originated deletes and calls through injected
    test executors — is blocked with one canonical error. A no-op for every
    other method.

    :param method: The Odoo model method about to be executed.
    :raises DeletionNotSupportedError: When ``method == "unlink"``.
    """
    if method == "unlink":
        raise DeletionNotSupportedError(_FORBIDDEN_DELETION_MESSAGE, method=method)
