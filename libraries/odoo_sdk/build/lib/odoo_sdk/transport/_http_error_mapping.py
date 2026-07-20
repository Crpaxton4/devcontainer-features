from __future__ import annotations

import json
from typing import Optional

from .errors import (
    OdooAccessError,
    OdooAuthenticationError,
    OdooError,
    OdooMissingRecordError,
    OdooServerError,
    OdooTransportError,
    OdooValidationError,
)

_NAME_MAP: dict[str, type[OdooError]] = {
    "odoo.exceptions.AccessDenied": OdooAuthenticationError,
    "odoo.exceptions.AccessError": OdooAccessError,
    "odoo.exceptions.MissingError": OdooMissingRecordError,
    "odoo.exceptions.ValidationError": OdooValidationError,
    "odoo.exceptions.UserError": OdooServerError,
}

_STATUS_MAP: dict[int, type[OdooError]] = {
    401: OdooAuthenticationError,
    403: OdooAccessError,
    404: OdooMissingRecordError,
    422: OdooValidationError,
}

_MAX_DETAIL_LENGTH = 500


def map_http_error(
    status_code: int,
    body: str,
    *,
    model: Optional[str] = None,
    method: Optional[str] = None,
) -> OdooError:
    """Map an HTTP error status and response body to an SDK exception.

    Shared by ``OdooJson2Executor`` (and any future JSON-2 transport) so error
    classification lives in one place: a JSON body's ``name`` maps by
    :data:`_NAME_MAP`, otherwise the status maps by :data:`_STATUS_MAP`, falling
    back to :class:`OdooServerError`.
    """
    try:
        data = json.loads(body)
    except ValueError:
        return OdooTransportError(
            "Non-JSON response received from server",
            model=model,
            method=method,
            detail=body[:_MAX_DETAIL_LENGTH],
        )

    name: str = data.get("name", "")
    message: str = (
        data.get("message", "") or data.get("error", "") or f"HTTP {status_code}"
    )
    debug: str = data.get("debug", "") or ""

    error_class = _NAME_MAP.get(name) or _STATUS_MAP.get(status_code) or OdooServerError

    return error_class(
        message,
        model=model,
        method=method,
        detail=debug or None,
    )
