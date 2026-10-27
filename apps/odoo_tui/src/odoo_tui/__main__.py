"""Console entry point for the btop-style Odoo session TUI.

Running ``odoo-tui`` or ``python -m odoo_tui`` opens the Textual TUI that
explores global sessions over a date window. The command service comes from the
composition root (:func:`odoo_tui.bootstrap.bootstrap`) and is wrapped in a
:class:`~odoo_tui.app.TuiDeps`.
"""

from .app import TuiDeps, run
from .bootstrap import assert_configured, bootstrap


def main() -> None:  # pragma: no cover
    """Check the configuration, bootstrap the command service, run the TUI."""
    assert_configured()
    run(TuiDeps(service=bootstrap()))


if __name__ == "__main__":
    main()
