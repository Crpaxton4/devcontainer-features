"""Connection settings — the shared-kernel value object the transports consume.

Extracted from :mod:`odoo_sdk.state.config` (#717) so the transport stack no
longer imports the local-state layer just to name its settings type and
default timeout (the ADR-005 debt register's "wrong direction" data→data
edge: transport is below state). This module is shared kernel: the value
object, its validators, and the timeout default are dependency-free, so any
layer may import them.

Resolution stays where it was: :class:`~odoo_sdk.state.config.LocalConfig`
remains the single settings *resolver* (File > Environment Variable >
Default) and re-exports everything here, so the ``odoo_sdk.state.config``
import paths keep working unchanged. :meth:`OdooConnectionSettings.
from_sources` is the one seam that touches the resolver, and it imports it
lazily inside the call so importing this module never pulls in the state
layer.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Optional

#: Default per-request transport timeout, in seconds. The single source of this
#: number: it is the fallback for garbage or absent ``ODOO_TIMEOUT`` values and the
#: dataclass field default, and both transports re-export it (as
#: ``DEFAULT_REQUEST_TIMEOUT_SECONDS``) rather than redefining the literal.
DEFAULT_TIMEOUT_SECONDS: float = 30.0

CONNECTION_ENV_VARS = {
    "url": "ODOO_URL",
    "db": "ODOO_DB",
    "username": "ODOO_USERNAME",
    "password": "ODOO_PASSWORD",
    "api_key": "ODOO_API_KEY",
    "transport": "ODOO_TRANSPORT",
    "timeout": "ODOO_TIMEOUT",
}


@dataclass(frozen=True)
class OdooConnectionSettings:
    """Resolved, validated connection settings consumed by the executor.

    One concrete set of connection strings distilled from explicit arguments,
    environment variables, and INI files.
    """

    url: str
    db: str
    username: Optional[str] = None
    password: Optional[str] = field(default=None, repr=False)
    transport: Literal["xmlrpc", "json2"] = "xmlrpc"
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    api_key: Optional[str] = field(default=None, repr=False)

    @classmethod
    def from_sources(
        cls,
        *,
        url: Optional[str] = None,
        db: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        api_key: Optional[str] = None,
        transport: Optional[str] = None,
        timeout: Optional[float] = None,
        config_path: Optional[str] = None,
    ) -> "OdooConnectionSettings":
        """Resolve connection settings via :class:`LocalConfig`, then validate.

        A thin validator over the single resolver: it resolves file, environment, and
        default values through :meth:`LocalConfig.load` (precedence **File >
        Environment Variable > Default**) and overlays any explicit constructor
        arguments (which win over every resolved source).

        The resolver import is deliberately function-local: the shared-kernel
        value object must not drag the state layer in at import time (#717).

        :raises ValueError: When any required setting remains unresolved.
        """
        from odoo_sdk.state.config import LocalConfig

        resolved: dict[str, Any] = dict(LocalConfig.load(config_path).connection)
        # Prefer explicit `None` checks so callers can pass empty strings
        # deliberately; validation still treats empty values as missing.
        explicit_values = {
            "url": url,
            "db": db,
            "username": username,
            "password": password,
            "api_key": api_key,
            "transport": transport,
            "timeout": timeout,
        }
        for key, explicit_value in explicit_values.items():
            if explicit_value is not None:
                resolved[key] = explicit_value
        return _build_connection_settings(resolved)


def _build_connection_settings(values: Mapping[str, Any]) -> OdooConnectionSettings:
    """Validate a resolved connection mapping and build the value object.

    The single place transport selection, required-setting validation, and timeout
    coercion happen, so :meth:`OdooConnectionSettings.from_sources` and
    :meth:`LocalConfig.connection_settings` behave identically.

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
