# Quickstart: the `odoo-tui` viewer

The btop-style terminal viewer for your tracked work, for the **human** at the
keyboard.

**There is no ingest step.** The timeline derives live from the local `events`
timeseries each time you open or move the window, so an empty screen is a fact
about the window, not a missing build step (see
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

A curses view: header (date window, session/task/event/hour counts), a
**timeline** panel beside a **stats** panel, and a keybind footer.

## The timeline and window model

The viewer shows one inclusive **date window**, ending today and spanning 7 days
on launch, and draws one lane per session from the same `query_sessions` command
the MCP surface exposes.

Sessions are **derived from events in SQL at query time**, gap-based: a task's
events group into a session until a gap larger than the session gap (default 60
minutes) splits them. Nothing is materialized — moving the window re-derives.

## Keybinds

The footer reads:

```text
 ←/→ start  ↑/↓ end  e:export  u:upload  r:resync  t:triage  v:review  q:quit
```

In full:

| Key(s) | Action |
|--------|--------|
| `←` / `→` | Move the window **start** date earlier / later |
| `↑` / `↓` | Move the window **end** date later / earlier |
| `e` | Export the window as **Markdown** to `timelog_<start>_<end>.md` in the current directory |
| `c` | Export the window as **CSV** to `timelog_<start>_<end>.csv` (the footer folds `e`/`c` into "export") |
| `u` | **Upload** the window's sessions to Odoo timesheets (behind a confirm gate) |
| `r` | **Resync** the current repo's events from git / GitHub / Odoo |
| `t` | **Triage** — assign task ids to unattributed events (`↑`/`↓` select, `0-9` task id, `⏎` assign, `s` skip, `q` back) |
| `v` | **Review** — session cards with confidence and overlap badges (`↑`/`↓` select, `e`/`⏎` evidence pane, `q` back); read-only, never uploads |
| `q` or `Esc` | Quit |

The window re-queries only when the dates change. Exports write to the working
directory and report the path on the status line.

### Upload (`u`) — anchor adoption and idempotent re-upload

Upload is the **only** thing that writes hours to Odoo (`stop_task` just ends the
run): your agent runs tasks all day, then you press `u` to bill the sessions.

`u` arms a confirm gate — `y` confirms, anything else cancels. Each session with
a numeric task id becomes a **single** timesheet row, resolved in three tiers:

1. **Mapped** — a prior upload for this session's stable `session_key` is on
   record; that row is rewritten.
2. **Adopt** — no mapping, but the task still carries its unreconciled
   `"[/] Work in progress"` anchor (the 0-hour row); its hours, description, and
   date are written in place.
3. **Create** — otherwise a fresh billed line.

An idempotent `session_uploads` ledger keyed by `session_key` records uploads, so
**re-uploading a window rewrites the same rows rather than double-billing.**
Sessions with no numeric task id are skipped.

#### Billed hours: minimum and rounding

A session bills its **wall-clock span** (first event to last). Raw span
under-bills at the small end — a single-event session spans zero time — so two
policies apply at the one point feeding both `u` and `odoo-sdk upload`:

- **Minimum** (`min_session_hours`, default `0.25`) — the span is floored *up* to
  this many hours, so a below-minimum session bills the minimum, never `0`.
- **Rounding** (`round_session_hours`, default `0.05`) — the span is rounded
  half-up to the nearest multiple of this step, then held at or above the
  minimum. A `1.87h` session bills `1.85h`; step `0` disables rounding.

There is **no cap**. Both knobs live in `[behavior]` or as
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
re-derives sessions. It runs **git** (commits), **github** (merged PRs and
reviews), and **odoo** (task chatter), and is:

- **current-repo scoped** — only the launch repo and the authenticated user;
- **manual** — only on `r`; nothing resyncs on a timer;
- **idempotent** — events dedupe by external id, so a re-run inserts nothing new.

The status line reports each source's inserted count, or a skip reason when its
tool is missing or unauthenticated (a skip is never fatal).

(reading-the-empty-state)=
## Reading the empty state

With no derivable sessions the panel shows a diagnostic line, then `log events
via start_task / odoo-sdk log-event, or widen the window`:

```text
no sessions derivable — 12 events in window, 3 runs recorded, gap=60m
```

- **events in window** — `0` means nothing happened here; `> 0` means data exists
  but does not sessionize here (wrong window, taskless events, or gap config).
- **runs recorded** — runs across all windows. Nonzero with `0` in-window events
  usually means wrong dates.
- **gap** — the session gap (minutes) splitting events into sessions.

So: `0` events → widen or move the window; `> 0` with no lanes → the events
aren't task-scoped (try `t`) or fall outside the gap grouping.

## Cleaning up stale runs in other projects (`odoo-sdk discover` / `abort`)

The tracker keys each project's database by a hash of its git remote, so a run
left open in a deleted checkout is invisible from any other working tree. The
`odoo-sdk` CLI clears these across every project database under the state root.

**Discover** lists each tracker project and its active runs — project hash, repo
label, run id, task, state, start time — flagging runs older than the staleness
threshold (default 12 hours) as `STALE`:

```bash
odoo-sdk discover                       # or: odoo-sdk discover --stale-after-hours 6
```

**Abort** force-closes one stale run and its orphaned anchor — but only while
that anchor is still the unreconciled `"[/] Work in progress"` marker; a
human-edited row is left untouched:

```bash
odoo-sdk abort <project_hash> <run_id>
```

Aborting logs no hours; it retires the wedged run and anchor so your timeline and
timesheets stay clean.

---

For how those runs and anchors are created, see {doc}`the MCP quickstart
<quickstart_mcp>`.
