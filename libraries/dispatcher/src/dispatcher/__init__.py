"""The host-side task dispatcher.

The only program on the machine that runs ``devcontainer`` or ``claude``. It
keeps its state in the suite's ``state.db`` through ``odoo_dev_data.jobs``.
"""
