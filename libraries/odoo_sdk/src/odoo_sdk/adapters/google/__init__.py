"""Google adapter package (#718): the ``CalendarGateway`` port's data side.

One package per external system (ADR-005 amendment, #718). This package owns
the SDK's only Google coupling — the opt-in Calendar meeting-tick and sent-
Gmail pullers, their host-provisioned-token credential handling, and the
injected stdlib-``urllib`` transport. The implementation lives in
:mod:`.sync`; this façade exports exactly the surface core consumes (the
:class:`~odoo_sdk.commands.protocols.CalendarGateway` port, which this
package satisfies structurally) plus the error/transport vocabulary callers
guard on.
"""

from .sync import (
    GoogleAPIError,
    GoogleAuthError,
    GoogleTransport,
    sync_gmail,
    sync_google_calendar,
)

__all__ = [
    "GoogleAPIError",
    "GoogleAuthError",
    "GoogleTransport",
    "sync_gmail",
    "sync_google_calendar",
]
