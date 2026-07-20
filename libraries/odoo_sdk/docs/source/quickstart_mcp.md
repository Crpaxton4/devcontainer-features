# Quickstart: the `odoo-mcp` tool surface

One `implement_task` session on the `odoo-mcp` server — for the **LLM agent**
calling the tools and the **human** supervising it.

Start the server with the `odoo-mcp` console script (or `python -m
odoo_sdk.mcp`). Arguments and return shapes: {doc}`API reference <api/modules>`.

## Contents

```{contents}
:local:
:depth: 1
```

## What the infrastructure does for you

Handled server-side; doing it by hand double-counts:

- **One event per tool call.** Exactly one `source="agent"` event per
  *successful* call (a raising call emits none), attributed to the task when the
  call carries `task_id`. There is no "emit event" or "log" tool.
- **Only the tool name is persisted, never argument values.** Note bodies,
  questions, and search queries stay out of the local events store (matching the
  `claude-event-hook` shim). What is *sent to Odoo* is unaffected.
- **Sessions are derived, not ingested.** No `ingest_sessions` step — they are
  computed from the `events` timeseries in SQL at query time, so
  `query_sessions` and `odoo-tui` always reflect current events.
- **Claude Code lifecycle hooks log themselves.** In a provisioned devcontainer
  they are forwarded to `odoo-sdk log-event` as `claude:<Hook>` events.
  `PreToolUse`/`PostToolUse` for this server's own tools (`mcp__odoo-mcp__*`,
  after its registered name) are skipped — the server already logs those
  dispatches, so they are counted once.

Call the tools in order and let the timeseries build itself. Do not self-log,
ingest, or reconcile.

## 1. Find the task

Start from a name, not an id:

```text
search_projects(query="Website")          -> [{id, name}, ...]
search_tasks(query="checkout bug", project_id=42)  -> [{id, name}, ...]
```

`get_task` always returns identity fields (name, project, stage, assignees,
deadline, priority, tags); `include` opts into expensive detail (`description`,
`chatter`, `dependencies`, `timesheets`, `subtasks`). Read the description and
chatter before writing code:

```text
get_task(task_id=1234, include=["description", "chatter"])
```

## 2. Start the task

First mutating call, and the only one that opens a run. `task_id` skips
name-search disambiguation:

```text
start_task(task_name_query="checkout bug", project_name_query="Website", task_id=1234)
```

Under supervision it elicits confirmations (task pick when ambiguous, a "start?"
gate, the base git branch), then atomically:

- **Enforces one active run per task.** A `RUNNING` or `AWAITING_ANSWERS` run on
  the task raises `TaskAlreadyRunningError` — call `task_status` first if unsure.
- **Writes no timesheet.** No `account.analytic.line` row; the former 0-hour
  `"[/] Work in progress"` anchor is gone (#325). All hours derive from captured
  events via the TUI upload path (step 6), the sole timesheet writer.
- Posts a `"Work started on this task."` chatter note and records the run locally.

Returns `run_id`, `task_id`, `started_at`, `timesheet_id` (`null` — no anchor).

## 3. Work, leaving `task_note` checkpoints

Notes require an active run and do **not** change FSM state:

```text
task_note(task_id=1234, note="Implementation plan:\n- reproduce with failing test\n- fix null coupon guard\n- add regression test")
```

Markdown in, HTML out. Keep each to a one-line summary plus 2–4 bullets; prefer
several notes at real checkpoints over one wall of text.

## 4. The AWAITING_ANSWERS detour

When only a stakeholder can decide, ask instead of guessing. `task_question`
posts a `[?]`-prefixed question and moves `RUNNING → AWAITING_ANSWERS`; further
questions self-loop:

```text
task_question(task_id=1234, question="Should expired coupons 404 or fall back to full price?")
```

`resume_task` posts a note and moves `AWAITING_ANSWERS → RUNNING`. Only then
continue:

```text
resume_task(task_id=1234)
```

## 5. Stop the task — no hours are written here

`stop_task` elicits a review/edit of the work description (the human's
checkpoint), moves the run `→ STOPPED`, and records the confirmed description
(prefixed `[/]`) locally:

```text
stop_task(task_id=1234, description="Fixed null-coupon crash in checkout; added regression test.")
```

**`stop_task` writes no timesheet hours.** Elapsed hours are computed and
returned for display only — every `account.analytic.line` write belongs to the
TUI upload path (`odoo-tui`, key `u`; see {doc}`the TUI quickstart
<quickstart_tui>`). With no active run it raises `TaskNotRunningError`.

## 6. Check state any time with `task_status`

Lists every active run (`RUNNING` or `AWAITING_ANSWERS`) for the current repo
with elapsed time. It changes nothing — the right first call when state is
unclear, such as after a `TaskAlreadyRunningError`:

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
- Besides `start_task`/`stop_task`, only `task_question` and `resume_task`
  change state; `task_note` and `task_status` never do.
- Errors return `{"error": {"type", "message"}}` — branch on `type`.
