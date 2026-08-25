"""Capability guard for the SDK's tracker commands."""

from odoo_sdk.state import LocalConfig, assert_tracker_db_present


def assert_sdk_configured() -> None:
    """Raise if the SDK's real preconditions are unmet (#642).

    The predecessor asserted three Odoo *runtime* markers (``ODOO_VERSION``,
    ``/etc/odoo/odoo.conf``, ``/mnt/extra-addons``) that nothing in this package
    ever reads, so a fully provisioned non-Odoo container was refused even
    though every tracker command would have worked in it. What the commands
    actually need is resolvable Odoo connection settings and the host-provisioned
    tracker DB, so those are what is checked.

    Both failures already have a named, actionable error that the MCP boundary
    (:data:`~odoo_sdk.mcp.server._BOUNDARY_ERRORS`) renders as a structured
    payload, so no new exception type is introduced.

    Only the *settings* are resolved — no :class:`~odoo_sdk.client.OdooClient`
    is built and no socket is opened — so the lazy-client contract holds.

    :raises ValueError: When required Odoo connection settings are unresolved.
    :raises TrackerStateMissingError: When the central tracker DB is absent.
    """
    LocalConfig.load().connection_settings()
    assert_tracker_db_present()
