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

# The legacy ``jobs.db`` (``odoo_sdk.state.jobs`` migrations 1 and 2), without
# the schema_migrations ledger the real store also carries.
LEGACY_JOBS_DDL = """
CREATE TABLE jobs (
    job_id        TEXT    PRIMARY KEY,
    batch_id      TEXT    NOT NULL,
    task_id       TEXT    NOT NULL CHECK (task_id <> ''),
    repo          TEXT    NOT NULL CHECK (repo <> ''),
    round         INTEGER NOT NULL DEFAULT 1 CHECK (round >= 1),
    state         TEXT    NOT NULL CHECK (state IN
        ('PENDING','RUNNING','DONE','DEAD_LETTER')),
    brief         TEXT    NOT NULL CHECK (brief <> ''),
    submitted_us  INTEGER NOT NULL CHECK (submitted_us > 0),
    started_us    INTEGER CHECK (started_us IS NULL OR started_us >= submitted_us),
    ended_us      INTEGER CHECK (ended_us   IS NULL OR ended_us   >= started_us),
    session_id    TEXT    NOT NULL DEFAULT '',
    container     TEXT    NOT NULL DEFAULT '',
    worktree      TEXT    NOT NULL DEFAULT '',
    outcome       TEXT    NOT NULL DEFAULT '',
    dead_reason   TEXT    NOT NULL DEFAULT '',
    detail        TEXT    NOT NULL DEFAULT '',
    entry         TEXT    NOT NULL DEFAULT '',
    CHECK (state <> 'PENDING' OR started_us IS NULL),
    CHECK (state <> 'RUNNING' OR (started_us IS NOT NULL AND ended_us IS NULL)),
    CHECK (state NOT IN ('DONE','DEAD_LETTER') OR ended_us IS NOT NULL),
    CHECK ((state = 'DEAD_LETTER') = (dead_reason <> ''))
) STRICT;
"""

# The legacy ``runs.db`` (``odoo_sdk.state.runs_store`` migration 1), without
# the append-only triggers and the generated columns.
LEGACY_RUNS_DDL = """
CREATE TABLE runs (
    session_id    TEXT    PRIMARY KEY,
    job_id        TEXT    NOT NULL,
    task_id       TEXT    NOT NULL CHECK (task_id <> ''),
    repo          TEXT    NOT NULL DEFAULT '',
    branch        TEXT    NOT NULL DEFAULT '',
    pr_num        INTEGER NOT NULL DEFAULT 0 CHECK (pr_num >= 0),
    started_us    INTEGER NOT NULL CHECK (started_us > 0),
    ended_us      INTEGER NOT NULL CHECK (ended_us >= started_us),
    outcome       TEXT    NOT NULL CHECK (outcome IN
        ('COMPLETED','COMPLETED_WITH_QUESTIONS','BLOCKED','FAILED')),
    tool_version  TEXT    NOT NULL CHECK (tool_version <> ''),
    transcript    TEXT    NOT NULL DEFAULT '',
    brief         TEXT    NOT NULL DEFAULT ''
) STRICT;

CREATE TABLE run_envelopes (
    session_id    TEXT PRIMARY KEY REFERENCES runs(session_id),
    envelope      TEXT NOT NULL CHECK (json_valid(envelope))
) STRICT;

CREATE TABLE run_artifacts (
    job_id        TEXT    PRIMARY KEY,
    session_id    TEXT    NOT NULL DEFAULT '',
    dir           TEXT    NOT NULL CHECK (dir <> ''),
    has_task_json INTEGER NOT NULL DEFAULT 0 CHECK (has_task_json IN (0,1)),
    has_session   INTEGER NOT NULL DEFAULT 0 CHECK (has_session   IN (0,1)),
    has_envelope  INTEGER NOT NULL DEFAULT 0 CHECK (has_envelope  IN (0,1)),
    bytes         INTEGER NOT NULL DEFAULT 0 CHECK (bytes >= 0),
    harvested_us  INTEGER NOT NULL CHECK (harvested_us > 0),
    missing       TEXT    NOT NULL DEFAULT ''
) STRICT;
"""
