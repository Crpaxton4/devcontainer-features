# Quickstart: the `odoo-tui` viewer

`odoo-tui` is the btop-style terminal viewer for your tracked work, for the
**human** at the keyboard.

**There is no ingest step.** The timeline is derived live from the local
`events` timeseries each time you open or move the window. An empty screen is a
fact about the window, not a missing build step (see
[Reading the empty state](reading-the-empty-state)).

## Contents

```{contents}
:local:
:depth: 2
```

## Launch

```bash
odoo-tui          # or: python -m odoo_sdk.tui
```

Opens a full-screen curses view: the header names the current date window and
headline counts (sessions, tasks, events, hours); the body is a **timeline**
panel beside a **stats** panel; the footer lists the keybinds.

## The timeline and window model

The viewer shows a single inclusive **date window**, ending today and spanning
the last 7 days on launch. Within it, `odoo-tui` calls the same `query_sessions`
command the MCP surface exposes and draws one timeline lane per derived session.

Sessions are **derived from events in SQL at query time**, gap-based: events for
a task group into a session until a gap larger than the session gap (default 60
minutes) splits them. Nothing is materialized; moving the window re-queries and
re-derives.

## Keybinds

The footer shows `←/→ start  ↑/↓ end  e:export  u:upload  r:resync  q:quit`. In
full:

| Key(s) | Action |
|--------|--------|
| `←` / `→` | Move the window **start** date earlier / later |
| `↑` / `↓` | Move the window **end** date later / earlier |
| `e` | Export the window as **Markdown** to `timelog_<start>_<end>.md` in the current directory |
| `c` | Export the window as **CSV** to `timelog_<start>_<end>.csv` (the footer folds `e`/`c` into "export") |
| `u` | **Upload** the window's sessions to Odoo timesheets (behind a confirm gate) |
| `r` | **Resync** the current repo's events from git / GitHub / Odoo |
| `q` or `Esc` | Quit |

Moving the window re-queries only when the dates change. Exports write to the
working directory and report the path on the status line.

### Upload (`u`) — anchor adoption and idempotent re-upload

Upload is the **only** thing that writes hours to Odoo (`stop_task` only ends the
run). The normal rhythm: your agent runs tasks all day, then you open `odoo-tui`
and press `u` to bill the derived sessions.

`u` arms a confirm gate — press `y` to confirm, any other key cancels. On
confirm, each derived session with a numeric task id is written to a **single**
timesheet row, resolved in three tiers:

1. **Mapped** — a prior upload for this session's stable `session_key` is on
   record; that same row is rewritten.
2. **Adopt** — no mapping yet, but the task still has its unreconciled
   `"[/] Work in progress"` anchor (the 0-hour row); the anchor is adopted — its
   hours, description, and date are written in place.
3. **Create** — otherwise a fresh billed line is created.

Uploads are recorded in an idempotent `session_uploads` ledger keyed by
`session_key`, so **re-uploading the same window rewrites the same rows rather
than double-billing.** Sessions with no numeric task id are skipped.

#### Billed hours: minimum and rounding

A session bills its **wall-clock span** (first event to last). Raw span
under-bills at the small end — a single-event session spans zero time — so the
upload path applies two policies at the one point feeding both `u` and
`odoo-sdk upload`:

- **Minimum** (`min_session_hours`, default `0.25`) — the span is floored *up* to
  this many hours; a below-minimum session bills the minimum, never `0`.
- **Rounding** (`round_session_hours`, default `0.05`) — the span is rounded to
  the nearest multiple of this step (half-up), then held at or above the minimum.
  A `1.87h` session bills `1.85h`. Step `0` disables rounding (raw span, still
  floored to the minimum).

There is **no cap**. Both knobs live in the `[behavior]` config section or as
`ODOO_MIN_SESSION_HOURS` / `ODOO_ROUND_SESSION_HOURS`, resolved **file >
environment > default**. An invalid value falls back to its default rather than
failing the upload:

```toml
[behavior]
min_session_hours = 0.25
round_session_hours = 0.05
```

### Resync (`r`) — three pullers, current-repo and manual only

Resync reconciles the local `events` table against external activity, then
re-derives sessions. It runs three pullers — **git** (your commits), **github**
(merged PRs and reviews), and **odoo** (your task chatter) — and is:

- **current-repo scoped** — only the launch repo and the authenticated user's
  activity;
- **manual** — only on `r`; nothing resyncs on a timer;
- **idempotent** — every event is deduped by external id, so a re-run inserts
  nothing new.

The status line reports each source's inserted count, or a skip reason when a
source's tool is missing or unauthenticated (a skip is never fatal).

(reading-the-empty-state)=
## Reading the empty state

When the query derives no sessions, the panel shows a diagnostic line:

```text
no sessions derivable — 12 events in window, 3 runs recorded, gap=60m
```

followed by `log events via start_task / odoo-sdk log-event, or widen the
window`. Read the counts as:

- **events in window** — events inside the queried dates. `0` means nothing
  happened here; `> 0` means data exists but does not sessionize here (wrong
  window, taskless events, or the gap config).
- **runs recorded** — task runs on record across all windows. Nonzero with `0`
  in-window events usually means wrong dates — move the window.
- **gap** — the session gap (minutes) used to split events into sessions.

So: if events-in-window is `0`, widen or move the window; if `> 0` with no lanes,
the events aren't task-scoped or fall outside the gap grouping.

## Cleaning up stale runs in other projects (`odoo-sdk discover` / `abort`)

The tracker keys each project's local database by a hash of its git remote, so a
run left open in a deleted checkout is invisible from any other working tree. The
`odoo-sdk` CLI finds and clears these across every project database under the
state root.

**Discover** lists every tracker project and its active runs, flagging any run
older than the staleness threshold (default 12 hours) as `STALE`:

```bash
odoo-sdk discover                       # or: odoo-sdk discover --stale-after-hours 6
```

Each row shows the project hash, repo label, run id, task, state, start time, and
stale flag. **Abort** then force-closes a specific stale run by project hash and
run id, closing its orphaned anchor — but only when the anchor is still the
unreconciled `"[/] Work in progress"` marker; a human-edited row is left
untouched:

```bash
odoo-sdk abort <project_hash> <run_id>
```

Aborting logs no hours; it retires the wedged run and its anchor so your timeline
and timesheets stay clean.

---

For how those runs and anchors are created, see {doc}`the MCP quickstart
<quickstart_mcp>`.
