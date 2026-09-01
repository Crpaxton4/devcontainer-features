"""Shared credential-refresh helpers for the retry-on-authentication-failure path.

Credentials are resolved exactly once at client construction, so a key rotated on
disk kept failing in-process until restart with an error indistinguishable from a
genuinely bad credential (issue #658). Both executors now accept an injected
zero-arg ``credentials_refresh`` callable and, on an authentication failure,
re-resolve settings through it and retry exactly once when they actually changed.
This module hosts the pieces both transports share: safely invoking the hook and
appending the rotation hint to the surfaced error. How settings resolve stays the
client's decision — the executors only receive the opaque callable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

from .errors import OdooAuthenticationError

if TYPE_CHECKING:  # pragma: no cover
    from odoo_sdk.state.config import OdooConnectionSettings

# Hint appended to an authentication error that survived the refresh-and-retry
# path (or had no refresh hook at all). Both variants contain the stable
# substring ``recently rotated`` so callers and tests can detect the hinted
# form without matching the full sentence.
ROTATION_HINT_REFRESHED = (
    "Authentication failed with identical credentials after re-reading the SDK "
    "config; if the API key/password was recently rotated, verify the config "
    "file holds the new value."
)
ROTATION_HINT_NO_REFRESH = (
    "Authentication failed; if the API key/password was recently rotated, "
    "restart the process to pick up the new configuration."
)


def call_refresh(
    refresh: Optional[Callable[[], "OdooConnectionSettings"]],
) -> Optional["OdooConnectionSettings"]:
    """Invoke the refresh hook, returning ``None`` when absent or failing.

    The hook re-reads the local config file, which may have become invalid since
    construction (``LocalConfig.load`` / ``from_sources`` raise ``ValueError`` on
    a now-broken file). The original authentication error must always win, so any
    exception the hook raises is swallowed and reported as "no fresh settings".
    """
    if refresh is None:
        return None
    try:
        return refresh()
    except Exception:
        return None


def with_rotation_hint(
    error: OdooAuthenticationError, hint: str
) -> OdooAuthenticationError:
    """Return a new :class:`OdooAuthenticationError` with ``hint`` appended.

    The structured metadata is copied from the original so the hinted error stays
    as actionable as the one it replaces. Credential values must never appear in
    either message. Callers raise the result ``from`` the original error.
    """
    return OdooAuthenticationError(
        f"{error} {hint}",
        operation=error.operation,
        model=error.model,
        method=error.method,
        fault_code=error.fault_code,
        fault_string=error.fault_string,
        detail=error.detail,
    )
