from __future__ import annotations

from typing import Optional


class OdooError(RuntimeError):
    """Base SDK exception for Odoo-facing failures."""

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
        super().__init__(message)
        self.operation = operation
        self.model = model
        self.method = method
        self.fault_code = fault_code
        self.fault_string = fault_string
        self.detail = detail


class OdooAuthenticationError(OdooError):
    """Raised when login or executor authentication fails."""


class OdooAccessError(OdooError):
    """Raised when the Odoo server denies access to an operation."""


class OdooValidationError(OdooError):
    """Raised when Odoo rejects input as invalid."""


class OdooMissingRecordError(OdooError):
    """Raised when an operation targets records that no longer exist."""


class OdooTransportError(OdooError):
    """Raised for local transport, protocol, or connectivity failures."""


class OdooServerError(OdooError):
    """Raised for unmapped or unexpected server-side XML-RPC faults."""