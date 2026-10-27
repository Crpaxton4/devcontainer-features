"""Connection settings: the shared-kernel value object the transports consume.

This module is dependency-free, so any layer may import it. It does not
resolve settings. the ``settings_from_config()`` of each app's ``bootstrap`` module turns a
:class:`odoo_dev_data.config.LocalConfig` into this value object through
:func:`build_connection_settings`.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Optional

#: Default per-request transport timeout, in seconds. The single source of this
#: number: it is the fallback for garbage or absent ``ODOO_TIMEOUT`` values and the
#: dataclass field default, and both transports re-export it (as
#: ``DEFAULT_REQUEST_TIMEOUT_SECONDS``) rather than redefining the literal.
DEFAULT_TIMEOUT_SECONDS: float = 30.0


@dataclass(frozen=True)
class OdooConnectionSettings:
    """Resolved, validated connection settings consumed by the executor.

    One concrete set of connection strings distilled from the resolved
    configuration.
    """

    url: str
    db: str
    username: Optional[str] = None
    password: Optional[str] = field(default=None, repr=False)
    transport: Literal["xmlrpc", "json2"] = "xmlrpc"
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    api_key: Optional[str] = field(default=None, repr=False)


def build_connection_settings(values: Mapping[str, Any]) -> OdooConnectionSettings:
    """Validate a resolved connection mapping and build the value object.

    The single place transport selection, required-setting validation, and timeout
    coercion happen.

    :raises ValueError: When any required setting remains unresolved.
    """
    resolved_transport: Literal["xmlrpc", "json2"] = (
        "json2" if values.get("transport") == "json2" else "xmlrpc"
    )
    _validate_required_settings(values, resolved_transport)
    return OdooConnectionSettings(
        url=str(values["url"]),
        db=str(values["db"]),
        username=values.get("username") or None,
        password=values.get("password") or None,
        transport=resolved_transport,
        timeout=_coerce_timeout(values.get("timeout")),
        api_key=values.get("api_key") or None,
    )


def _validate_required_settings(
    values: Mapping[str, Any],
    transport: Literal["xmlrpc", "json2"],
) -> None:
    """Raise ValueError when required settings are absent for the given transport."""
    if transport == "json2":
        required = ("url", "db", "api_key")
        missing = [key for key in required if not values.get(key)]
    else:
        required = ("url", "db", "username", "password")
        missing = [key for key in required if values.get(key) in (None, "")]

    if missing:
        missing_names = ", ".join(sorted(missing))
        raise ValueError(
            "Missing Odoo connection settings: "
            f"{missing_names}. Configure them with environment variables, "
            "the config file, or override them with constructor arguments."
        )


def _coerce_non_negative_float(value: Any, default: float) -> float:
    """Coerce a raw value to a non-negative finite float, else ``default``.

    Values arrive from environment variables and INI files as strings, may be
    absent, or may be garbage; anything that is not a finite number ``>= 0``
    degrades to ``default`` rather than raising, so a mistyped config never crashes
    an upload. Booleans are rejected explicitly because ``float(True)`` is ``1.0``,
    which would silently turn ``= true`` into a one-hour floor.
    """
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isfinite(number) and number >= 0:
        return number
    return default


def _coerce_timeout(value: Any) -> float:
    """Coerce a raw timeout to a strictly-positive float, else :data:`DEFAULT_TIMEOUT_SECONDS`."""
    coerced = _coerce_non_negative_float(value, DEFAULT_TIMEOUT_SECONDS)
    return coerced if coerced > 0 else DEFAULT_TIMEOUT_SECONDS
