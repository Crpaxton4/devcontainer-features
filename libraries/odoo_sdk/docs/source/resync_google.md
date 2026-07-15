# Resync: Google Calendar and sent mail

`resync` reconciles the local `events` table against external history. Alongside
the built-in `git`, `github`, and `odoo` pullers it can ingest two **opt-in**
Google sources — meetings you actually attended and mail you actually sent — so
sessionization stops under-billing the part of the day spent in calls and
correspondence.

Both are **opt-in**: they are never run by the default source string and only
execute when named explicitly:

```bash
odoo-sdk resync --sources gcal,gmail     # Google only
odoo-sdk resync --sources git,github,odoo,gcal,gmail   # everything
odoo-sdk resync                          # default — Google NOT included
```

## What is ingested

**Calendar — participation only.** A meeting is ingested only when you
**organized** it or your response is **accepted**. Declined, tentative, and
never-answered invites, cancelled events, all-day blocks, out-of-office / focus /
busy furniture, and solo blocks (no other attendees) are all excluded.

An ingested meeting is expanded into a **tick train**: synthetic point events
`calendar_tick_mins` minutes apart (default 5), with a terminal tick on the
**exact** end time. A 10:00–11:00 meeting becomes 13 ticks; a 12-minute meeting
becomes ticks at 0, 5, 10, and 12. The unchanged gap-based derivation then
reconstructs the meeting as **one** session of the true duration — no schema
change and no new session strategy. Because ticks are ordinary point events, a
meeting about task X and coding on task X merge into a single billed lane, while
a meeting on task A running parallel to work on task B stays two lanes.

**Email — sent mail only.** Only messages you **sent** are ingested (Gmail
`in:sent`); received mail, CCs, list traffic, and automated notifications are
never stored — receiving is not billable participation. A sent message is a
single point event carrying metadata only (message-id, thread-id, participants,
direction, timestamp) — **never the body**.

## Attribution

An event is attributed to a task only by an **explicit marker** in the meeting
title or email subject (`#24648`, `[24648]`, or a bare ≥4-digit id). Without one
the event is ingested with `task_ids = []` — inert for billing by design — and is
surfaced for triage rather than guessed at. Wrong attribution is worse than none.

## Reconcile is delete-and-re-expand

Calendar events mutate retroactively, so `resync` looks both backward and forward
`google_sync_window_days` days (default 30) and reconciles each meeting's tick
series **by its parent event id**: an unchanged meeting is left untouched (so a
triage task assignment on its ticks survives), while a reschedule, extension,
shortening, or cancellation deletes the old series and re-expands the new one —
leaving **no orphan ticks** and creating **no duplicates**. A re-`resync` over an
overlapping window is a no-op.

## The tick-interval invariant

The tick interval must stay **strictly below** both the session gap and the
sweep floor. Otherwise a meeting's ticks stop chaining into one session and each
tick bills the per-session minimum, turning one hour-long meeting into many
minimum-billed sessions. `resync` validates this and **rejects** a configuration
that violates it with a clear error, rather than silently shattering meetings.

The synthetic ticks are excluded from the `optimize_sessions` gap sweep (they
would otherwise dominate its event population), but they always participate in
session derivation.

## Credentials are host-provisioned

Like the tracker database itself, Google credentials are provisioned **on the
host**, never minted inside the container. Run the stdlib-only helper once on the
host to authorize read-only Calendar and Gmail access and write a token into the
existing `~/.config/odoo_sdk` mount:

```bash
python3 scripts/google_oauth_setup.py \
    --client-id  <CLIENT_ID>.apps.googleusercontent.com \
    --client-secret <CLIENT_SECRET> \
    --output ~/.config/odoo_sdk/google_token.json
```

The scopes requested are `calendar.readonly` and `gmail.readonly`. The SDK only
**consumes** the token (refreshing it via a plain token-endpoint POST when it
goes stale) — it never runs the OAuth flow. A missing, expired, or unrefreshable
credential raises a **single actionable error** naming the token path and the
fix; silently ingesting zero events is treated as a failure, not a success.

## Configuration

These `[behavior]` settings (config file, or the matching `ODOO_*` environment
variable) tune ingestion:

| Setting | Default | Meaning |
| --- | --- | --- |
| `calendar_tick_mins` | `5` | Meeting expansion tick interval (minutes). |
| `ingest_subjects` | `true` | Store meeting/email subjects; set `false` to keep titles out of the DB. |
| `google_sync_window_days` | `30` | Backward/forward reconcile window radius (days). |
| `google_token_path` | *(derived)* | Explicit token file path; defaults under the `ODOO_SDK_CONFIG` mount. |

No third-party Google client library is required — the pullers reach the REST
APIs directly over stdlib `urllib`.
