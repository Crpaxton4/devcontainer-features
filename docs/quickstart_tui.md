# Quickstart: the `odoo-tui` viewer

`odoo-tui` is a terminal viewer for your tracked work. It is for the human at the
keyboard. It is the `odoo_tui` app of the suite, and it is built with
[Textual](https://textual.textualize.io/).

There is no ingest step. The viewer derives the timeline each time you open it or
move the window. An empty screen means the window has no sessions. See
[Reading the empty state](reading-the-empty-state).

## Launch

```bash
odoo-tui          # or: python -m odoo_tui
```

The viewer reads `$ODOO_DEV_HOME/config.toml` and the environment, and it stops if
a required connection setting is missing. It shows a header (date window and
counts), a timeline panel beside a stats panel, a status line and a footer with the
keys.

## Sessions and job windows

The window is one inclusive date range. It ends today and spans 7 days at launch.
The viewer draws one lane for each session. It gets the sessions from the
`query_sessions` command, the same command that the MCP server offers.

Two kinds of window become sessions:

- **Event sessions.** The events of one task form a session until a gap longer
  than the session gap splits them. The default gap is 60 minutes
  (`[behavior] session_gap_mins`, `ODOO_SESSION_GAP_MINS`).
- **Job windows.** A finished dispatcher job with a start and an end time is a
  session of its own (see {doc}`design/jobs`). Its session key is
  `<task_id>|run:<session_id>`.

Nothing is stored. Moving the window derives the sessions again. The viewer has no
run views: a run is a job, and it appears as a job window in the timeline.

## Keys

```text
 ←/→ start  ↑/↓ end  e:export  c:csv  u:upload  r:resync  t:triage  v:review  q:quit
```

| Key | Action |
|-----|--------|
| `←` / `→` | Move the start date of the window earlier or later |
| `↑` / `↓` | Move the end date of the window later or earlier |
| `e` | Export the window as Markdown to `timelog_<start>_<end>.md` in the current directory |
| `c` | Export the window as CSV to `timelog_<start>_<end>.csv` |
| `u` | Upload the sessions of the window to Odoo timesheets, after a confirmation |
| `r` | Resync events from git, GitHub and Odoo chatter |
| `t` | Triage: assign task ids to events that have none |
| `v` | Review: session cards with confidence and overlap badges. It is read-only |
| `q` or `Esc` | Quit, or go back from a screen |

In the triage screen, use `↑` and `↓` to select an event, type the task id with
the digit keys, press `⏎` to assign it and `s` to skip. In the review screen, use
`↑` and `↓` to select a card and `e` or `⏎` to show the evidence.

## Upload

The upload is the only way to write hours to Odoo from the viewer. Press `u`, then
`y` to confirm. Any other key cancels. The `odoo-dev upload` command runs the same
`upload` command from a shell.

Each session with a numeric task id becomes one timesheet row. The `session_key`
makes the upload idempotent. A second upload of the same window rewrites the same
rows and does not bill twice. A session without a numeric task id is skipped. The
status line shows the number of billed, retired and failed sessions.

A session bills its wall-clock span, from the first event to the last. Two
settings of `[behavior]` change that span:

- `min_session_hours` (default `0.25`, `ODOO_MIN_SESSION_HOURS`) raises the span
  to at least this value.
- `round_session_hours` (default `0.05`, `ODOO_ROUND_SESSION_HOURS`) rounds the
  span half-up to a multiple of this step. A step of `0` turns rounding off.

There is no upper limit. The order of precedence is file, environment, default.
A value that is not valid falls back to its default.

## Resync

Press `r` to reconcile the events with outside history. Three pullers run: git
(commits), GitHub (pull requests, reviews and comments) and Odoo (task chatter).
The git puller finds every checkout under the launch directory. The GitHub puller
searches the account of the signed-in user. Resync runs only when you press the key,
and a second run inserts nothing new, because events have an external id. The
status line shows what each source inserted, or why it was skipped. From a shell,
run `odoo-dev resync --start <date> --end <date>`.

(reading-the-empty-state)=
## Reading the empty state

When the window has no session, the viewer shows a diagnostic line:

```text
no sessions derivable — 12 events in window, gap=60m
```

- `0` events: nothing happened in the window. Widen the window or move it.
- More than `0` events and no lane: the events do not belong to a task, or the gap
  setting splits them. Press `t` to assign the events to tasks.
