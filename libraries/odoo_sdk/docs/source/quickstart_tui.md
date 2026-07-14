# Quickstart: the `odoo-tui` viewer

This is a walkthrough of `odoo-tui`, the btop-style terminal viewer for your
tracked work — written for the **human** at the keyboard. It covers launching,
the timeline/window model, every keybind, the empty-state diagnostics, and the
`odoo-sdk discover`/`abort` flow for cleaning up stale runs left in other
projects.

**There is no ingest step.** The timeline is derived live from the local
`events` timeseries every time you open or move the window, so whatever your
agent and hooks have logged is already there. If the screen is empty, that is a
fact about the window, not a missing build step — the empty-state hint tells you
which (see [Reading the empty state](reading-the-empty-state)).

## Launch

```bash
odoo-tui          # or: python -m odoo_sdk.tui
```

It opens a full-screen curses view. The header names the current date window and
the headline counts (sessions, tasks, events, hours); the body is a **timeline**
panel beside a **stats** panel; the footer lists the keybinds.

## The timeline and window model

The viewer always shows a single inclusive **date window**. On launch that
window ends today and spans the last 7 days. Within it, `odoo-tui` calls the
same `query_sessions` command the MCP surface exposes and draws one timeline lane
per derived session.

Sessions are **derived from events in SQL at query time** — gap-based: events
for a task are grouped into a session until a gap larger than the configured
session gap (default 60 minutes) splits them. Nothing is materialized, so the
timeline always reflects the current events. Moving the window re-queries and
re-derives; you never rebuild anything.

## Keybinds

The footer shows: `←/→ start  ↑/↓ end  e:export  u:upload  r:resync  q:quit`.
In full:

| Key(s) | Action |
|--------|--------|
| `←` / `→` | Move the window **start** date earlier / later |
| `↑` / `↓` | Move the window **end** date later / earlier |
| `e` | Export the window as **Markdown** to `timelog_<start>_<end>.md` in the current directory |
| `c` | Export the window as **CSV** to `timelog_<start>_<end>.csv` (the footer folds `e`/`c` into "export") |
| `u` | **Upload** the window's sessions to Odoo timesheets (behind a confirm gate) |
| `r` | **Resync** the current repo's events from git / GitHub / Odoo |
| `q` or `Esc` | Quit |

Moving the window only re-queries when the dates actually change. Exports write a
file to the working directory and report the path on the status line.

### Upload (`u`) — anchor adoption and idempotent re-upload

Upload is the **only** thing that writes hours to Odoo. `stop_task` never does;
it only ends the run. So the normal rhythm is: your agent runs tasks all day
(each `start_task` leaving a 0-hour `"[/] Work in progress"` anchor), then you
open `odoo-tui` and press `u` to bill the derived sessions.

Pressing `u` arms a confirm gate — the status line asks you to press `y` to
confirm (any other key cancels). On confirm, each derived session with a numeric
task id is written to a **single** timesheet row, resolved in three tiers:

1. **Mapped** — a prior upload for this session's stable `session_key` is on
   record; that same row is rewritten.
2. **Adopt** — no mapping yet, but the task still has its unreconciled
   `"[/] Work in progress"` anchor (the 0-hour row `start_task` created); the
   anchor is **adopted** — its hours, description, and date are written in place.
3. **Create** — otherwise a fresh billed line is created.

Because uploads are recorded in an idempotent `session_uploads` ledger keyed by
`session_key`, **re-uploading the same window rewrites the same rows rather than
double-billing.** Sessions with no numeric task id are skipped (they have no
Odoo task to bill).

### Resync (`r`) — three pullers, current-repo and manual only

Resync reconciles the local `events` table against external activity, then
re-derives sessions so anything new appears immediately. It runs three pullers —
**git** (your authored commits), **github** (merged PRs and reviews), and
**odoo** (your task chatter) — and is:

- **current-repo scoped** — it reads only the repo you launched from and the
  authenticated user's activity;
- **manual** — it runs only when you press `r` (nothing resyncs on a timer);
- **idempotent** — every event is deduped by external id, so a re-run inserts
  nothing new.

The status line reports each source's inserted count, or a skip reason when a
source's tool is missing or unauthenticated (a skipped source is never fatal).

(reading-the-empty-state)=
## Reading the empty state

An empty window is never a silent dead end. When the query derives no sessions,
the panel shows a diagnostic line of the form:

```text
no sessions derivable — 12 events in window, 3 runs recorded, gap=60m
```

followed by the guidance `log events via start_task / odoo-sdk log-event, or
widen the window`. Read the counts as:

- **events in window** — how many events fall inside the queried dates. `0`
  means nothing happened in this window; a number `> 0` means data exists but
  does not sessionize *here* (wrong window, taskless events, or the gap config).
- **runs recorded** — how many task runs are on record overall, across all
  windows. A nonzero value with `0` events in-window usually just means you are
  looking at the wrong dates — move the window.
- **gap** — the session gap (in minutes) the deriver uses to split events into
  sessions.

So: if events-in-window is `0`, widen or move the window (or the work genuinely
predates it); if it is `> 0` but you still see no lanes, the events aren't
task-scoped or fall outside the gap grouping.

## Cleaning up stale runs in other projects (`odoo-sdk discover` / `abort`)

The tracker keys each project's local database by a hash of its git remote, so a
run left open in a checkout you've since deleted becomes invisible from any other
working tree — and its 0-hour anchor stays open in Odoo. The `odoo-sdk` CLI
finds and clears these across every project database under the state root.

**Discover** lists every tracker project and its active runs, flagging any run
older than the staleness threshold (default 12 hours) as `STALE`:

```bash
odoo-sdk discover                       # or: odoo-sdk discover --stale-after-hours 6
```

Each row shows the project hash, repo label, run id, task, state, start time, and
the stale flag. **Abort** then force-closes a specific stale run by its project
hash and run id, closing out its orphaned anchor (only when the anchor is still
the unreconciled `"[/] Work in progress"` marker — a human-edited row is left
untouched):

```bash
odoo-sdk abort <project_hash> <run_id>
```

Aborting logs no hours; it simply retires the wedged run and its anchor so your
timeline and timesheets stay clean.

---

For how those runs and anchors are created in the first place, see
{doc}`the MCP quickstart <quickstart_mcp>`.
