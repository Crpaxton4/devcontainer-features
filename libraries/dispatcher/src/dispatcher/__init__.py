"""The host-side task dispatcher.

The only program on the machine that runs ``devcontainer`` or ``claude``. It
owns its persistence layer, :mod:`dispatcher.store`.
"""
