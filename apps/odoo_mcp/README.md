# odoo_mcp

The MCP server app of the odoo-dev suite (UI layer). It installs the `odoo-mcp`
console script (`odoo_mcp.__main__:main`, also `python -m odoo_mcp`). The server
talks over stdio and is built on FastMCP.

## Tools

There is no hand-written tool list. `odoo_mcp/tools.py` calls
`registry.specs("mcp")` and builds one tool per command spec. The tool signature
is the `Args` dataclass of the command, so the wire schema always matches the
command. The tool name is the command id, and the description is the spec
description. `odoo_mcp/server.py` wraps each tool with an error boundary, usage
logging and event capture. `bootstrap.py` is the composition root.

`get_models` is the one gated command: it reads the administrative `ir.model`
table (`gated=True` on its spec).

## Configuration

Connection settings come from `config.toml` in `ODOO_DEV_HOME` or from
`ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`, `ODOO_PASSWORD` or `ODOO_API_KEY`,
`ODOO_TRANSPORT` and `ODOO_TIMEOUT`. `ODOO_TOON_OUTPUT=1` returns results in TOON
form. `--profiling` or `ODOO_PROFILING` profiles each call.

## Register with Claude Code

```
claude mcp add odoo-mcp -- odoo-mcp
```

The `personal-features` Feature registers the server at container create.

## Dependencies

`odoo_dev_core`, `fastmcp`, `toon-format`.

## Test

```
uv run --package odoo_mcp pytest apps/odoo_mcp/tests
```
