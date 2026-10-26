"""The host-side task dispatcher.

The only program on the machine that runs ``devcontainer`` or ``claude``. It
depends on :mod:`odoo_client` (its persistence layer) and never the reverse.
"""
