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

The idempotent lifecycle entry point (#621), and the only call that opens a
run. `task_id` alone is sufficient and skips name-search disambiguation and
every prompt (headless-safe):

```text
start_task(task_id=1234)
start_task(task_name_query="checkout bug", project_name_query="Website")
```

On the name path it elicits disambiguation (project/task pick when ambiguous,
the base git branch), then atomically dispatches on the session state:

- **Idempotent per task.** Already `RUNNING` → no-op success returning the
  existing run with `already_running: true` (zero side effects);
  `AWAITING_ANSWERS` → back to `RUNNING`; non-aborted `STOPPED` → resumed in
  place; aborted/closed/none → a fresh run.
- **Writes no timesheet.** No `account.analytic.line` row; the former 0-hour
  `"[/] Work in progress"` anchor is gone (#325). All hours derive from captured
  events via the TUI upload path (step 6), the sole timesheet writer.
- Posts no chatter note; the run and its events are the tracking record.

Returns `run_id`, `task_id`, `state`, `already_running`, `started_at`,
`timesheet_id` (`null` — no anchor).

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

`stop_task` moves the run `→ STOPPED` with no prompt at all (#623) and stores a
machine-derived run summary — computed from the run's recorded events and notes
(#626) — on the run row, where the billing upload picks it up as the timesheet
entry's description:

```text
stop_task(task_id=1234)
```

**`stop_task` writes no timesheet hours.** Elapsed hours are computed and
returned for display only — every `account.analytic.line` write belongs to the
TUI upload path (`odoo-tui`, key `u`; see {doc}`the TUI quickstart
<quickstart_tui>`). With no active run it raises `TaskNotRunningError`.

## 6. Check state any time with `task_status`

Lists every active run (`RUNNING` or `AWAITING_ANSWERS`) for the current repo
with elapsed time. It changes nothing — the right first call when state is
unclear:

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
stop_task             → STOPPED  (derives the run summary; writes NO hours)
```

- Every mutating tool after `start_task` needs an active run, else
  `TaskNotRunningError`.
- `start_task` is idempotent (#621): a second call on a `RUNNING` task no-ops
  with `already_running: true` instead of erroring.
- Besides `start_task`/`stop_task`, only `task_question` and `resume_task`
  change state; `task_note` and `task_status` never do.
- Errors return `{"error": {"type", "message"}}` — branch on `type`.
