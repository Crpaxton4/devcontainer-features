"""Console entry point of the Odoo MCP server (``odoo-mcp`` or ``python -m odoo_mcp``)."""

import argparse
from typing import Optional, Sequence

from odoo_mcp.bootstrap import LocalConfig, assert_configured, bootstrap
from odoo_mcp.server import OdooMCPServer


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Parse the flags, bootstrap the command service and run the server on stdio.

    Per-call profiling comes from ``[behavior] profiling`` or ``ODOO_PROFILING``
    unless ``--profiling`` is given.
    """
    parser = argparse.ArgumentParser(prog="odoo-mcp", description="Odoo MCP server")
    parser.add_argument("--profiling", action="store_true", help="profile each call")
    flags = parser.parse_args(argv)
    config = LocalConfig.load()
    assert_configured(config)
    service = bootstrap(config=config)
    OdooMCPServer(service, profiling=flags.profiling or config.profiling).run()


if __name__ == "__main__":
    main()
