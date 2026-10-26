"""The numbered migrations of the single ``state.db``.

The job row is the only run record: there is no ``task_runs`` table, no
``runs.db`` and no ``run_artifacts``. Time is integer microseconds UTC in
every ``*_us`` column. ``started_us`` and ``ended_us`` are the billable window.

Migrations are frozen text. Every literal is inlined, including the ten
dead-letter reasons, so a later change to a constant elsewhere in the package
cannot alter what an old migration does.
"""

from __future__ import annotations

from .engine import Migration

MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        1,
        "events",
        (
            """CREATE TABLE events (
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
) STRICT""",
            "CREATE INDEX idx_events_timestamp ON events (timestamp)",
            """CREATE UNIQUE INDEX idx_events_external_id
    ON events(external_id) WHERE external_id IS NOT NULL""",
            """CREATE TABLE session_uploads (
    session_key  TEXT PRIMARY KEY,
    timesheet_id INTEGER NOT NULL,
    hours        REAL NOT NULL,
    uploaded_at  TEXT NOT NULL CHECK(datetime(uploaded_at) IS NOT NULL),
    task_id      TEXT,
    started_at   TEXT          CHECK(started_at IS NULL OR datetime(started_at) IS NOT NULL),
    ended_at     TEXT          CHECK(ended_at IS NULL OR datetime(ended_at) IS NOT NULL)
) STRICT""",
            """CREATE TABLE chatter_dedupe (
    task_id    INTEGER NOT NULL,
    dedupe_key TEXT    NOT NULL,
    message_id INTEGER NOT NULL,
    created_at TEXT    NOT NULL CHECK(datetime(created_at) IS NOT NULL)
) STRICT""",
            """CREATE UNIQUE INDEX idx_chatter_dedupe_key
    ON chatter_dedupe(task_id, dedupe_key)""",
            """CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT""",
        ),
    ),
    Migration(
        2,
        "jobs",
        (
            """
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
    dead_reason   TEXT    NOT NULL DEFAULT '' CHECK (dead_reason IN (
        '',
        'DUPLICATE_TASK_IN_FLIGHT',
        'CONTAINER_START_FAILED',
        'WORKSPACE_MOUNT_INVALID',
        'SESSION_LAUNCH_FAILED',
        'KILLED_BY_DEADLINE',
        'KILLED_BY_SIGNAL',
        'SESSION_EXIT_NONZERO',
        'STRUCTURED_OUTPUT_MISSING',
        'STRUCTURED_OUTPUT_UNREADABLE',
        'ORPHANED_BY_RESTART')),
    detail        TEXT    NOT NULL DEFAULT '',
    entry         TEXT    NOT NULL DEFAULT '',
    branch               TEXT    NOT NULL DEFAULT '',
    pr_num               INTEGER NOT NULL DEFAULT 0 CHECK (pr_num >= 0),
    tool_version         TEXT    NOT NULL DEFAULT '',
    envelope             TEXT    NOT NULL DEFAULT '' CHECK (envelope = '' OR json_valid(envelope)),
    transcript_path      TEXT    NOT NULL DEFAULT '',
    bundle_dir           TEXT    NOT NULL DEFAULT '',
    has_task_json        INTEGER NOT NULL DEFAULT 0 CHECK (has_task_json IN (0,1)),
    has_session          INTEGER NOT NULL DEFAULT 0 CHECK (has_session IN (0,1)),
    has_envelope         INTEGER NOT NULL DEFAULT 0 CHECK (has_envelope IN (0,1)),
    bytes                INTEGER NOT NULL DEFAULT 0 CHECK (bytes >= 0),
    harvested_us         INTEGER CHECK (harvested_us IS NULL OR harvested_us > 0),
    missing              TEXT    NOT NULL DEFAULT '',
    input_tokens         INTEGER NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens        INTEGER NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    cost_usd             REAL    NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
    approved_us          INTEGER,
    reported_us          INTEGER,
    friction_recorded_us INTEGER,
    stop_requested_us    INTEGER,
    question_count       INTEGER NOT NULL DEFAULT 0 CHECK (question_count >= 0),
    submitted_at  TEXT GENERATED ALWAYS AS
        (strftime('%Y-%m-%dT%H:%M:%fZ', submitted_us/1000000.0, 'unixepoch')) VIRTUAL,
    CHECK (state <> 'PENDING' OR started_us IS NULL),
    CHECK (state <> 'RUNNING' OR (started_us IS NOT NULL AND ended_us IS NULL)),
    CHECK (state NOT IN ('DONE','DEAD_LETTER') OR ended_us IS NOT NULL),
    CHECK ((state = 'DEAD_LETTER') = (dead_reason <> ''))
) STRICT""",
            "CREATE UNIQUE INDEX idx_jobs_one_live ON jobs(task_id) "
            "WHERE state IN ('PENDING','RUNNING')",
            "CREATE INDEX idx_jobs_state ON jobs(state, submitted_us)",
        ),
    ),
)
