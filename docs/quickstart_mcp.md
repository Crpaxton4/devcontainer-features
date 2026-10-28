# Quickstart: the `odoo-mcp` server

The `odoo-mcp` server gives an LLM agent 32 tools over Odoo. It is for the agent
that calls the tools and for the human who supervises it. The server is the
`odoo_mcp` app of the suite.

## Start the server

Configure the connection first (see the connection section of the
{doc}`start page <index>`). Then start the server. It speaks MCP on stdio.

```bash
odoo-mcp                    # or: python -m odoo_mcp
odoo-mcp --profiling        # also write a cProfile zip for each tool call
```

The server reads `$ODOO_DEV_HOME/config.toml` and the environment, and it stops at
start if a required connection setting is missing. `[behavior] profiling` or
`ODOO_PROFILING` turn on profiling without the flag. `ODOO_TOON_OUTPUT=1` returns
structured results as TOON text, which uses fewer tokens. A client such as Claude
Code registers the server by its command, `odoo-mcp`.

## Where the tools come from

The tools are generated. `odoo_mcp.tools.build_tools` takes every command spec of
`registry.specs("mcp")` and builds one tool. The name, the description and the
arguments of the tool come from the command and its `Args` dataclass. The server
registers tools only. It has no prompts. Three commands are not tools:
`get_employee_id`, `log_event` and `upload` run through `odoo-dev cmd` only.

To see the commands of the CLI surface, run `odoo-dev cmd --list`. For the
arguments of each command, read the {doc}`API reference
<api/odoo_dev_core/modules>`.

## The tools

| Group | Tools |
|-------|-------|
| Find a task | `search_projects`, `search_tasks`, `task_list`, `task_aging`, `get_tasks`, `get_todo`, `get_task`, `search_count` |
| Read a task | `get_task_chatter`, `get_task_attachments`, `read_attachment`, `search_chatter`, `get_mail_status` |
| Knowledge | `search_knowledge_articles`, `read_knowledge_article` |
| Write to Odoo | `create_task`, `task_note`, `task_question`, `schedule_activity`, `mark_activity_done` |
| Activities | `get_activities`, `search_activity_types` |
| Time and billing | `timesheet_summary`, `unbilled_hours`, `unlogged_time_report`, `query_sessions`, `optimize_sessions`, `normalize_timesheets`, `assign_event`, `resync` |
| Identity and setup | `get_uid`, `get_models` |

Start from a name, not an id:

```text
search_projects(query="Website")                   -> [{id, name}, ...]
search_tasks(query="checkout bug", project_id=42)  -> [{id, name}, ...]
```

`get_task` always returns the identity fields (name, project, stage, assignees,
deadline, priority, tags). The `include` argument adds costly detail. Read the
description and the chatter before you write code:

```text
get_task(task_id=1234, include=["description", "chatter"])
```

## Notes and questions

`task_note` posts a progress note to the task chatter. The note is Markdown and
Odoo shows it as HTML. `task_question` posts a question with the `[?]` prefix.
Both limit the body to 500 characters. Neither needs an active run, and neither
changes any state. Keep each note to one summary line and two to four bullets.

```text
task_note(task_id=1234, note="Plan:\n- reproduce with a failing test\n- fix the coupon guard")
task_question(task_id=1234, question="Should an expired coupon return 404 or full price?")
```

When the server runs inside a dispatched job, `ODOO_JOB_ID` is set.
`task_question` then adds one to the `question_count` of that job (see
{doc}`design/jobs`).

## What the server does for you

- **One event for each successful call.** The server writes one `source="agent"`
  event after a tool call succeeds. A call that raises writes none. There is no
  tool to write an event by hand.
- **Only names are stored.** The event keeps the tool name, the argument names and
  a one-line outcome. It does not keep note bodies, questions or search queries.
  See {doc}`design/event-record-and-payload-policy`.
- **Attribution is limited.** A tool that changes a task (`task_note`,
  `task_question`, `create_task`, `schedule_activity`, `mark_activity_done`)
  attributes the event to the task it names. A read tool does not.
- **A dispatched run writes no event.** When `ODOO_UNATTENDED` is set, the job row
  measures the run.
- **Errors are data.** A caller error returns `{"error": {"type", "message"}}`.
  Branch on `type`.
- **Sessions are derived.** `query_sessions` computes sessions from the events and
  the finished jobs at query time. Nothing is ingested.

## Model ids

`get_models` reads `ir.model`, which a least-privileged account cannot do. It is
marked `gated` in the registry, but the server does not hide it. Call it once as
an operator with `{"persist": [...]}` to write ids into `[model_ids]`. See the
{doc}`start page <index>`.
