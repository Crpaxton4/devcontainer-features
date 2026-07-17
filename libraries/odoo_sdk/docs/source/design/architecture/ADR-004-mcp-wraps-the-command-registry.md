# ADR-004 - MCP Wraps the Command Registry

Status: Accepted

Date: 2026-07-15

## Context

- Phase H embeds a Model Context Protocol (MCP) server in the SDK so LLM agents can operate on Odoo through the MCP tool + prompt protocol.
- The original Phase H tool PRD ([`h3-mcp-tools.prd.md`](../implementation/phase-h/h3-mcp-tools.prd.md)) proposed nine generic ORM tools (`search`, `read`, `search_read`, `create`, `write`, `unlink`, `name_search`, `read_group`, `fields_get`) that would wrap `OdooRecordset`, with MCP prompts explicitly out of scope.
- That plan did not ship. The SDK already owns a command `Registry` (`odoo_sdk/commands`) whose builtin commands encode the task-tracker workflow (start/stop/resume/abort a task, chatter and knowledge search, timesheet and session reporting, …). Re-implementing a second, parallel tool surface over raw recordsets would have duplicated that logic and drifted from it.
- No design doc stated the contract the code actually implements, which is the gap this ADR closes.

## Decision

- **MCP wraps the command registry.** The MCP tool surface is a projection of the builtin command `Registry`, not a generic ORM CRUD surface over `OdooRecordset`.
- Atomic tools are **1:1 typed wrappers over builtin commands**. Each factory in `odoo_sdk/mcp/tools/atomic.py` (registered in `ATOMIC_TOOL_FACTORIES` via the `@atomic_tool(name)` decorator) returns a plainly written function with a real, typed signature that delegates to the like-named command via `registry["..."].execute(...)`.
- **Wire schemas are intentionally hand-written**, not reflected from `command.execute` signatures. `OdooMCPServer` (`odoo_sdk/mcp/server.py`) performs no auto-reflection: the tool surface is exactly what `explicit_tools` provides, keeping the wire schema a reviewable part of the interaction surface.
- **Composition tools coexist with atomic tools.** `start_task` and `stop_task` (`COMPOSITION_TOOL_FACTORIES` in `odoo_sdk/mcp/tools/__init__.py`) additionally take the FastMCP `ctx` and orchestrate `ctx.elicit` interactions; `build_explicit_tools` merges the atomic and composition factories into one `explicit_tools` mapping.
- **Prompts are registered alongside tools.** `register_builtin_prompts` (`odoo_sdk/mcp/prompts/builtin`) registers the builtin prompt set (currently 8, spanning the task-tracker prompts and the ported personal-features consulting skills) to the same FastMCP instance.

## Implementation Status

- 37 atomic tools ship in `odoo_sdk/mcp/tools/atomic.py` (e.g. `get_task`, `create_task`, `task_question`, `resume_task`, `abort_task`, `abort_run`, `discover_runs`, `resync`, `timesheet_summary`, `unbilled_hours`, `unlogged_time_report`, `query_sessions`, `search_chatter`, `search_knowledge_articles`, …), each backed by a builtin command in `odoo_sdk/commands/builtin`.
- Two composition tools (`start_task`, `stop_task`) ship. Eight prompts ship: `implement_task`, `report_incident`, and six prompts ported from personal-features consulting skills (`client_status_report`, `discovery_notes`, `fibonacci_estimate`, `odoo_code_review`, `odoo_design_doc`, `odoo_quote`) via PR #464.
- None of the nine generic ORM tools from the Phase H PRD exist. `h3-mcp-tools.prd.md` is marked historical/superseded and points here.
- The server is built on FastMCP (`fastmcp` dependency) and is exposed both as a library (`OdooMCPServer`, lazily importable as `odoo_sdk.OdooMCPServer`) and via the `odoo-mcp` console entry point (`odoo_sdk/mcp/__main__.py`), which builds the default registry with `register_builtins` and runs the server over stdio.

## Consequences

Positive consequences
- The MCP surface and the command registry cannot drift apart: a tool is a thin typed wrapper over a command that already exists and is tested.
- Hand-written wire schemas keep the agent-facing contract explicit and reviewable, independent of internal `execute` signatures.
- The task-tracker workflow (not generic ORM CRUD) is the surface LLM agents actually need, and it reuses the SDK's existing guards (e.g. `forbid_unlink`, FSM state guards).

Negative consequences
- Every new tool requires an explicit factory and hand-written signature; there is no automatic tool generation from commands.
- The atomic-tool name and the backing command name are decoupled, so a duplicate public name is a registration-time error (guarded by `atomic_tool`) that must be avoided by hand.

## Rejected alternatives

- Nine generic ORM tools wrapping `OdooRecordset` (the original PRD).
  - Rejected because it would duplicate and drift from the command registry, and because unrestricted generic CRUD is not the surface the task-tracker workflow needs (and `unlink` is forbidden SDK-wide).
- Auto-reflecting tool schemas from `command.execute` signatures.
  - Rejected because it would make the agent-facing wire schema an accident of internal signatures rather than an intentional, reviewable contract.
