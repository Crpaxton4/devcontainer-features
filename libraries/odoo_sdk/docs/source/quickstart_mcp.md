# Quickstart: the `odoo-mcp` tool surface

This is a walkthrough of one complete `implement_task` session driven through
the `odoo-mcp` server — written for the **LLM agent** calling the tools and for
the **human** supervising it. It follows a single task from discovery to a
clean stop, and calls out what happens automatically so the agent never wastes a
turn re-doing work the infrastructure already does.

The server is started by the `odoo-mcp` console script (or
`python -m odoo_sdk.mcp`). Every tool named below is one MCP tool on that
surface; the reference for each tool's exact arguments and return shape is
generated separately (see {doc}`the API reference <api/modules>`) — this page is
the *narrative*, not the schema.

## What the infrastructure does for you

Read this first. Several things that look like the agent's job are handled
server-side, and trying to do them by hand is wasted effort (or actively
double-counts):

- **One event per tool call, automatically.** The MCP server wraps every tool
  dispatch and appends exactly one `source="agent"` event to the local state
  store on a *successful* call. A tool that raises emits no event. The event is
  attributed to the task when the call carries a `task_id`, and left
  session-level otherwise. You never call an "emit event" or "log" tool — there
  isn't one, and the FSM commands no longer self-log.
- **Sessions are derived, not ingested.** There is no `ingest_sessions` step and
  no materialized sessions table. Sessions are computed from the `events`
  timeseries in SQL at query time, so `query_sessions` (and the `odoo-tui`
  timeline) always reflect the current events with zero manual steps.
- **Claude Code lifecycle hooks log themselves.** In a provisioned devcontainer,
  Claude Code's own lifecycle events (`SessionStart`, `PreToolUse`,
  `PostToolUse`, `SubagentStart`/`SubagentStop`, …) are forwarded to
  `odoo-sdk log-event` as `claude:<Hook>` events by a feature-installed hook
  shim. `PreToolUse`/`PostToolUse` for `mcp__odoo__*` tools are deliberately
  skipped there, because the MCP server already logs those dispatches
  server-side — so they are counted once, not twice.

The practical upshot: **do the task, call the tools in order, and let the
timeseries build itself.** Do not try to self-log, ingest, or reconcile events.

## 1. Find the task

Start from a name, not an id. `search_projects` narrows to a project, and
`search_tasks` returns id/name candidates *within* that project:

```text
search_projects(query="Website")          -> [{id, name}, ...]
search_tasks(query="checkout bug", project_id=42)  -> [{id, name}, ...]
```

Once you have a candidate id, pull context with `get_task`. Base identity fields
(name, project, stage, assignees, deadline, priority, tags) always come back;
the `include` list opts into the more expensive detail — `description`,
`chatter`, `dependencies`, `timesheets`, `subtasks`. Read the description and
chatter before writing any code:

```text
get_task(task_id=1234, include=["description", "chatter"])
```

## 2. Start the task

`start_task` is the first mutating call of the session and the only one that
opens a run. Pass the resolved identity; supplying `task_id` skips name-search
disambiguation:

```text
start_task(task_name_query="checkout bug", project_name_query="Website", task_id=1234)
```

Under the human's supervision the tool elicits a few confirmations (a
project/task pick when ambiguous, then a "start?" gate, then the base git branch
to fork from), then does three things atomically:

- **State guard — one active run per task.** If the task already has an active
  run (`RUNNING` or `AWAITING_ANSWERS`), `start_task` raises
  `TaskAlreadyRunningError` instead of opening a second one. Call `task_status`
  first if you are unsure whether a run is already open.
- **Anchor semantics.** It creates a single **0-hour** `"[/] Work in progress"`
  timesheet anchor for task-board visibility. The anchor is *adopted* if one
  already exists, so a repeated start never duplicates it. This anchor carries
  **no hours** — hours are written later, and only by the TUI upload (see step
  6).
- Posts a `"Work started on this task."` note to the task chatter and records
  the run locally.

It returns the `run_id`, `task_id`, `started_at`, and the `timesheet_id` of the
anchor.

## 3. Work, leaving `task_note` checkpoints

As you implement, drop short progress notes with `task_note`. A note requires an
active run and does **not** change FSM state:

```text
task_note(task_id=1234, note="Implementation plan:\n- reproduce with failing test\n- fix null coupon guard\n- add regression test")
```

Notes render as **HTML in the chatter** (Markdown in, HTML out), so keep them
short and scannable: a one-line summary, then 2–4 bullets — not long prose.
Prefer several small notes at real checkpoints (plan formed, approach chosen,
tests passing) over one wall of text.

## 4. The AWAITING_ANSWERS detour

When you hit something only a stakeholder can decide, don't guess — ask.
`task_question` posts a `[?]`-prefixed question to the chatter and transitions
the run `RUNNING → AWAITING_ANSWERS`. Additional questions are allowed while
awaiting (the state self-loops):

```text
task_question(task_id=1234, question="Should expired coupons 404 or fall back to full price?")
```

When the answer arrives and you can proceed, `resume_task` posts a note and
transitions back `AWAITING_ANSWERS → RUNNING`:

```text
resume_task(task_id=1234)
```

Only then continue implementing (and drop more `task_note` checkpoints).

## 5. Stop the task — no hours are written here

When implementation is complete, `stop_task` closes the run. It elicits a
review/edit of the work description (the human's checkpoint), then transitions
the run `→ STOPPED` and records the confirmed description (prefixed `[/]`)
locally:

```text
stop_task(task_id=1234, description="Fixed null-coupon crash in checkout; added regression test.")
```

**`stop_task` does not write timesheet hours.** Say it plainly: stopping only
ends the run. The 0-hour anchor from step 2 is left untouched, and the elapsed
hours are computed and returned for display but *not* posted to Odoo. All
`account.analytic.line` hour writes are owned by the TUI upload path
(`odoo-tui`, key `u`) — see {doc}`the TUI quickstart <quickstart_tui>`. If there
is no active run, `stop_task` raises `TaskNotRunningError`.

## 6. Check state any time with `task_status`

`task_status` lists every active run (`RUNNING` or `AWAITING_ANSWERS`) for the
current repo with elapsed time. It changes nothing and is the right first call
whenever you are unsure of the current state — for example after a
`TaskAlreadyRunningError`:

```text
task_status()  -> [{run_id, task_id, task_name, state, started_at, elapsed}, ...]
```

## Ordering and dependencies at a glance

```text
search_projects / search_tasks / get_task   (read-only discovery, any order)
        │
        ▼
start_task            opens the one run; creates the 0-hour anchor
        │
        ▼
task_note ...         checkpoints (needs an active run; no state change)
        │
        ├─ task_question → AWAITING_ANSWERS ─ resume_task → RUNNING ─┐
        │                                                            │
        ▼                                                            │
stop_task             → STOPPED  (records description; writes NO hours)
```

- Every mutating tool after `start_task` needs an active run; calling one
  without a run raises `TaskNotRunningError`.
- `start_task` refuses a second concurrent run (`TaskAlreadyRunningError`).
- `task_question` and `resume_task` are the only state-changers besides
  `start_task`/`stop_task`; `task_note` and `task_status` never change state.
- Errors the caller can act on come back as a structured
  `{"error": {"type", "message"}}` payload rather than a stack trace, so you can
  branch on `type`.

Everything in the event/session/billing pipeline downstream of these calls is
automatic: the per-call events accrue, sessions derive from them at query time,
and the hours are written when a human runs the TUI upload.
