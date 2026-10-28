# ADR-007 - No Agent Framework: Claude Code Is the Runtime

Status: Accepted

Date: 2026-10-28

## Context

- After the 9.0 restructure the question came up whether the suite should be
  built around an agent framework such as LangChain (with LangGraph and Deep
  Agents) or pydantic-ai (with pydantic-graph and the harness add-on).
- The suite contains no model call. No package under `libraries/` or `apps/`
  and no plugin script imports an LLM client. The agent loop runs in Claude
  Code: the dispatcher starts `claude -p <entry> <task>` inside the project
  devcontainer (`dispatcher.lifecycle.render_exec`), and the host orchestrator
  (`/odoo-orchestrator:run`) is itself a Claude Code session.
- The dispatcher is a process supervisor: admission, `devcontainer up` and
  `devcontainer exec`, the `stream-json` parser, the deadline, the `STOP`
  file, the job row and the bundle. The MCP server is a projection of the
  command registry that any MCP client can consume. The gates are plugin hooks
  in bash and JavaScript.
- ADR-006 and the 9.0 plan state that the agents in the dispatched job sessions
  are the state machine, not commands. An earlier rigid orchestration script
  was retired because it failed, and a third-party orchestrator was rejected
  for duplicating the dispatcher and for per-agent processes that lose the
  shared skills and MCP servers.

## Options considered

1. Keep Claude Code as the job runtime. No framework.
2. Replace the `claude -p` subprocess with the Claude Agent SDK. Same engine,
   typed messages instead of `stream-json`. The session runs behind
   `devcontainer exec`, so the SDK would need a runner inside the container.
   The subprocess is already the documented path across that boundary.
3. Replace Claude Code inside the container with a pydantic-ai harness Coder
   or a LangChain Deep Agent. Useful only for models from other providers.
   Rewrites the plugin agents, skills, hooks and entry commands. The harness
   packages are 0.x. The line count goes up.
4. One targeted structured model call from Python, for example a semantic
   brief lint or a batch evaluation. Needs a direct API key and separate
   billing, which the suite does not have.

## Decision

- Option 1. Claude Code stays the runtime of every job session and of the
  orchestrator. The workspace does not add LangChain, LangGraph, pydantic-ai
  or the Claude Agent SDK.
- Models are Anthropic only. Every model call goes through a Claude Code
  login. No Python code calls the model API with its own key.
- Durable job state stays the job row in `state.db`. LangGraph checkpoints or
  a Temporal workflow would duplicate it.
- A semantic check of a brief, when wanted, happens inside the orchestrator
  session, not in a Python model call.

## Consequences

- The MCP server and the command registry stay framework-neutral. Any MCP
  client can use them, so this decision costs nothing if it changes later.
- The decision is revisited only if models from other providers or direct API
  billing become goals.
