# ADR-004 - MCP Wraps the Command Registry

Status: Accepted (amended 2026-10)

Date: 2026-07-15

## Context

- A Model Context Protocol (MCP) server lets LLM agents operate on Odoo through
  a tool protocol.
- The first plan proposed nine generic ORM tools (`search`, `read`,
  `search_read`, `create`, `write`, `unlink`, `name_search`, `read_group`,
  `fields_get`) over `OdooRecordset`, with MCP prompts out of scope. That plan
  did not ship.
- The suite already had a command registry whose builtin commands encode the
  real workflow: task, chatter, knowledge, timesheet and session reporting.
  A second tool surface over raw recordsets would duplicate that logic and drift
  from it.

## Decision

- **MCP wraps the command registry.** The MCP tool surface is a projection of the
  registry, not a generic ORM CRUD surface.
- Each tool is a typed wrapper over one command and runs it through the command
  service.
- Static instructional text is not an SDK concern. The `odoo-dev` plugin owns
  the consulting skills.

## Amendment (2026-10): the tools are generated

The decision stands. The way the projection is built changed. This section
replaces the 2026-07 mechanism, which used hand-written wire schemas, composition
tools with `ctx.elicit` and two dynamic prompts.

- The registry is `odoo_dev_core.registry`: `CommandSpec`, the `@command`
  decorator, `default_registry()` and `CommandService`. It holds 35 builtin
  commands.
- The MCP tools are generated from `registry.specs("mcp")`.
  `odoo_mcp.tools.build_tools` builds one function per spec. The signature of the
  function is the `Args` dataclass of the command, so the wire schema always
  matches the command. The server has 32 tools. Three commands are on the `cli`
  surface only: `get_employee_id`, `log_event` and `upload`.
- Each tool runs through `CommandService.execute`. `odoo_mcp.server` wraps the
  call in a usage log, one agent event for each successful call, an error
  boundary and optional TOON output.
- The `gated` flag of `CommandSpec` marks a command that needs a privilege that a
  least-privileged service account does not hold. `get_models` is the one gated
  command, because it reads `ir.model`. `odoo_mcp.tools` does not read the flag
  today, so `get_models` is one of the 32 tools. The flag only marks the command.
  A surface may use it to hide the command, and no surface does yet.
- The MCP prompts are gone. The composition tools and the run state machine are
  deleted. The server registers tools only. Task orchestration is the dispatcher
  job (see {doc}`../jobs`), not an MCP tool.

## Consequences

Positive
- The MCP surface and the registry cannot drift apart. A new command with the
  `mcp` surface becomes a tool with no further code.
- The CLI, the TUI and the MCP server share one command implementation.

Negative
- The wire schema follows the `Args` dataclass. A change to a dataclass changes
  the agent-facing contract, so review it as one.

## Rejected alternatives

- **Nine generic ORM tools** (the first plan). Rejected: they duplicate and drift
  from the registry, and unrestricted CRUD is not the surface the workflow needs.
- **Hand-written wire schemas** (the 2026-07 mechanism). Replaced: they needed an
  explicit factory per tool and drifted from the command signatures.
