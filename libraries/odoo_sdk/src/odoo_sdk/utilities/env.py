"""Environment guard for Odoo devcontainer-only operations."""

import os
from pathlib import Path


class OdooDevcontainerRequiredError(RuntimeError):
    """Raised when a command is run outside an Odoo devcontainer."""


def assert_odoo_devcontainer() -> None:
    """Raise if the current environment is not an Odoo devcontainer."""
    checks = [
        (os.environ.get("ODOO_VERSION"), "ODOO_VERSION env var is not set"),
        (Path("/etc/odoo/odoo.conf").exists(), "/etc/odoo/odoo.conf not found"),
        (Path("/mnt/extra-addons").exists(), "/mnt/extra-addons not found"),
    ]
    for ok, msg in checks:
        if not ok:
            raise OdooDevcontainerRequiredError(
                f"Not running in an Odoo devcontainer: {msg}"
            )
