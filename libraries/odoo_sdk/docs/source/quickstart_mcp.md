# Quickstart: the `odoo-mcp` tool surface

One complete `implement_task` session driven through the `odoo-mcp` server —
for the **LLM agent** calling the tools and the **human** supervising it. It
follows a single task from discovery to a clean stop.

Start the server with the `odoo-mcp` console script (or `python -m
odoo_sdk.mcp`). Every tool below is one MCP tool on that surface; for exact
arguments and return shapes see {doc}`the API reference <api/modules>`.

## Contents

```{contents}
:local:
:depth: 1
```

## What the infrastructure does for you

Read this first. These are handled server-side; doing them by hand is wasted
effort or double-counts.

- **One event per tool call, automatically.** The server appends exactly one
  `source="agent"` event on each *successful* call (a raising call emits none),
  attributed to the task when the call carries a `task_id`. There is no
  "emit event" or "log" tool.
- **Only the tool name is persisted, never argument values.** Note bodies,
  stakeholder questions, and search queries stay out of the local events store
  (matching the `claude-event-hook` shim). What is *sent to Odoo* is unaffected.
- **Sessions are derived, not ingested.** There is no `ingest_sessions` step;
  sessions are computed from the `events` timeseries in SQL at query time, so
  `query_sessions` and the `odoo-tui` timeline always reflect current events.
- **Claude Code lifecycle hooks log themselves.** In a provisioned devcontainer,
  Claude Code's lifecycle events are forwarded to `odoo-sdk log-event` as
  `claude:<Hook>` events. `PreToolUse`/`PostToolUse` for `mcp__odoo__*` are
  skipped there, since the server already logs those dispatches — counted once.

Upshot: do the task, call the tools in order, and let the timeseries build
itself. Do not self-log, ingest, or reconcile events.

## 1. Find the task

Start from a name, not an id. `search_projects` narrows to a project;
`search_tasks` returns id/name candidates within it:

```text
search_projects(query="Website")          -> [{id, name}, ...]
search_tasks(query="checkout bug", project_id=42)  -> [{id, name}, ...]
```

Pull context with `get_task`. Identity fields (name, project, stage, assignees,
deadline, priority, tags) always return; `include` opts into expensive detail
(`description`, `chatter`, `dependencies`, `timesheets`, `subtasks`). Read the
description and chatter before writing code:

```text
get_task(task_id=1234, include=["description", "chatter"])
```

## 2. Start the task

`start_task` is the first mutating call and the only one that opens a run.
Passing `task_id` skips name-search disambiguation:

```text
start_task(task_name_query="checkout bug", project_name_query="Website", task_id=1234)
```

Under supervision it elicits a few confirmations (task pick when ambiguous, a
"start?" gate, the base git branch to fork from), then atomically:

- **Enforces one active run per task.** If the task already has a `RUNNING` or
  `AWAITING_ANSWERS` run, it raises `TaskAlreadyRunningError`. Call `task_status`
  first if unsure.
- **Writes no timesheet.** No `account.analytic.line` row is created — the former
  0-hour `"[/] Work in progress"` anchor is gone (#325). All hours derive from
  captured events via the TUI upload path (step 6), the sole timesheet writer.
- Posts a `"Work started on this task."` chatter note and records the run
  locally.

Returns `run_id`, `task_id`, `started_at`, and `timesheet_id` (`null` — no
anchor).

## 3. Work, leaving `task_note` checkpoints

Drop short progress notes with `task_note`. A note requires an active run and
does **not** change FSM state:

```text
task_note(task_id=1234, note="Implementation plan:\n- reproduce with failing test\n- fix null coupon guard\n- add regression test")
```

Notes render as HTML in the chatter (Markdown in, HTML out). Keep them short: a
one-line summary plus 2–4 bullets. Prefer several small notes at real
checkpoints over one wall of text.

## 4. The AWAITING_ANSWERS detour

When only a stakeholder can decide, ask instead of guessing. `task_question`
posts a `[?]`-prefixed question and transitions `RUNNING → AWAITING_ANSWERS`;
further questions self-loop:

```text
task_question(task_id=1234, question="Should expired coupons 404 or fall back to full price?")
```

When the answer arrives, `resume_task` posts a note and transitions
`AWAITING_ANSWERS → RUNNING`. Only then continue (and drop more checkpoints):

```text
resume_task(task_id=1234)
```

## 5. Stop the task — no hours are written here

`stop_task` closes the run. It elicits a review/edit of the work description
(the human's checkpoint), transitions the run `→ STOPPED`, and records the
confirmed description (prefixed `[/]`) locally:

```text
stop_task(task_id=1234, description="Fixed null-coupon crash in checkout; added regression test.")
```

**`stop_task` writes no timesheet hours.** Stopping only ends the run. Elapsed
hours are computed and returned for display but not posted to Odoo — all
`account.analytic.line` writes are owned by the TUI upload path (`odoo-tui`,
key `u`; see {doc}`the TUI quickstart <quickstart_tui>`). With no active run it
raises `TaskNotRunningError`.

## 6. Check state any time with `task_status`

`task_status` lists every active run (`RUNNING` or `AWAITING_ANSWERS`) for the
current repo with elapsed time. It changes nothing and is the right first call
whenever state is unclear — for example after a `TaskAlreadyRunningError`:

```text
task_status()  -> [{run_id, task_id, task_name, state, started_at, elapsed}, ...]
```

## Ordering and dependencies at a glance

```text
search_projects / search_tasks / get_task   (read-only discovery, any order)
        │
        ▼
start_task            opens the one run (no timesheet write)
        │
        ▼
task_note ...         checkpoints (needs an active run; no state change)
        │
        ├─ task_question → AWAITING_ANSWERS ─ resume_task → RUNNING ─┐
        │                                                            │
        ▼                                                            │
stop_task             → STOPPED  (records description; writes NO hours)
```

- Every mutating tool after `start_task` needs an active run, else
  `TaskNotRunningError`.
- `start_task` refuses a second concurrent run (`TaskAlreadyRunningError`).
- `task_question` and `resume_task` are the only state-changers besides
  `start_task`/`stop_task`; `task_note` and `task_status` never change state.
- Actionable errors come back as `{"error": {"type", "message"}}`, so you can
  branch on `type`.
