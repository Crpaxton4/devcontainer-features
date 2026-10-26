"""The legacy ``tracker.db`` schema (version 4), frozen as a test fixture.

Copied verbatim from ``odoo_sdk.state.db.SCHEMA_DDL``, ``task_runs`` included.
"""

LEGACY_USER_VERSION = 4

LEGACY_TRACKER_DDL = """
CREATE TABLE IF NOT EXISTS task_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      INTEGER NOT NULL,
    task_name    TEXT    NOT NULL,
    project_id   INTEGER NOT NULL,
    project_name TEXT    NOT NULL,
    state        TEXT    NOT NULL CHECK(state IN ('RUNNING', 'AWAITING_ANSWERS', 'STOPPED', 'CLOSED')),
    started_at   TEXT    NOT NULL CHECK(datetime(started_at) IS NOT NULL),
    stopped_at   TEXT             CHECK(stopped_at IS NULL OR datetime(stopped_at) IS NOT NULL),
    timesheet_id INTEGER,
    notes        TEXT    NOT NULL DEFAULT '[]' CHECK(json_valid(notes)),
    aborted_at   TEXT             CHECK(aborted_at IS NULL OR datetime(aborted_at) IS NOT NULL),
    question_message_id INTEGER,
    run_summary  TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT    NOT NULL,
    timestamp   TEXT    NOT NULL CHECK(datetime(timestamp) IS NOT NULL),
    task_ids    TEXT    NOT NULL DEFAULT '[]' CHECK(json_valid(task_ids)),
    repo        TEXT    NOT NULL DEFAULT '',
    pr_num      INTEGER NOT NULL DEFAULT 0,
    branch      TEXT    NOT NULL DEFAULT '',
    subject     TEXT    NOT NULL DEFAULT '',
    payload     TEXT,
    external_id TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events (timestamp);

CREATE UNIQUE INDEX IF NOT EXISTS idx_events_external_id
    ON events(external_id) WHERE external_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS session_uploads (
    session_key  TEXT PRIMARY KEY,
    timesheet_id INTEGER NOT NULL,
    hours        REAL NOT NULL,
    uploaded_at  TEXT NOT NULL CHECK(datetime(uploaded_at) IS NOT NULL),
    task_id      TEXT,
    started_at   TEXT          CHECK(started_at IS NULL OR datetime(started_at) IS NOT NULL),
    ended_at     TEXT          CHECK(ended_at IS NULL OR datetime(ended_at) IS NOT NULL)
) STRICT;

CREATE TABLE IF NOT EXISTS chatter_dedupe (
    task_id    INTEGER NOT NULL,
    dedupe_key TEXT    NOT NULL,
    message_id INTEGER NOT NULL,
    created_at TEXT    NOT NULL CHECK(datetime(created_at) IS NOT NULL)
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_chatter_dedupe_key
    ON chatter_dedupe(task_id, dedupe_key);
"""
