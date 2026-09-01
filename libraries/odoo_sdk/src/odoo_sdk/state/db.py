"""SQLite-backed FSM state for task time-tracking sessions."""

import json
import os
import sqlite3
import subprocess
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from odoo_sdk._utils import as_utc

from .models import (
    EventRecord,
    InvalidStateTransitionError,
    SessionWindow,
    TaskAlreadyRunningError,
    TaskNotRunningError,
    TaskRun,
    TaskState,
    TrackerStateMissingError,
)

#: Filename of the single central tracker database under the state root (#369).
#: There is exactly one host-provisioned DB per user — events, ``task_runs``, and
#: the upload ledger all live in it — so ``repo`` is an ordinary column keyed on
#: the normalized ``owner/repo`` label rather than a per-repo directory hash.
TRACKER_DB_FILENAME = "tracker.db"

# Repo-less agent events cannot key on a real repository, so their derived
# sessions carry an ABSENT repo — the empty string — rather than an in-band
# sentinel value (#508). The empty string is never a real ``owner/repo``, so such
# events still group deterministically in the SQL-derived read path
# (:meth:`LocalStateClient.derive_sessions_overlapping`), and unlike the old
# ``"\x00agent"`` sentinel it carries no control character into JSON, MCP
# responses, or a rendered TUI screen. It is also exactly what the in-Python
# derivation (:mod:`odoo_sdk.sessionization.transform`) already produced, so the
# two paths no longer diverge for the same input.
AGENTLESS_REPO = ""

#: Deprecated alias for :data:`AGENTLESS_REPO`, kept so out-of-tree callers that
#: still compare against or filter on the old name keep working (#508). Both
#: names are the same absent-repo value, so ``repo == AGENTLESS_REPO_SENTINEL``
#: and ``repo=AGENTLESS_REPO_SENTINEL`` filters behave as before.
AGENTLESS_REPO_SENTINEL = AGENTLESS_REPO

#: Printable stand-in shown wherever an absent repo needs a human label.
AGENTLESS_REPO_LABEL = "(agent)"


def format_repo_label(repo: Optional[str]) -> str:
    """Return the display label for a session's ``repo``.

    The ONE place absent-repo display is decided (#508). Every consumer — the TUI
    lane labels, MCP/CLI JSON renderers, exports — routes through this helper so
    they agree, instead of each masking the absent value independently.
    """
    return repo if repo else AGENTLESS_REPO_LABEL


# Max ids bound into a single ``... IN (...)`` statement (delete and series-assign).
# Chunked to stay well under SQLite's historical 999-variable limit; each caller
# runs all chunks inside one transaction so the whole operation stays atomic.
_ID_CHUNK = 500


def _chunks(seq: Sequence[int], size: int = _ID_CHUNK):
    """Yield successive ``size``-length slices of ``seq``."""
    for start in range(0, len(seq), size):
        yield seq[start : start + size]


def _default_root() -> Path:
    """Resolve the user-writable base directory for tracker state.

    Precedence: ``$XDG_STATE_HOME/odoo-task-tracker`` when ``XDG_STATE_HOME``
    is set, otherwise ``~/.local/state/odoo-task-tracker``.
    """
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg_state_home) if xdg_state_home else Path.home() / ".local" / "state"
    return base / "odoo-task-tracker"


#: Schema version stamped into ``PRAGMA user_version``. History: ``0`` was the
#: implicit pre-#452 non-STRICT schema; ``1`` adopted the STRICT typed schema
#: (write-time validation); ``2`` added the terminal ``CLOSED`` state to the
#: ``task_runs.state`` CHECK (#504); ``3`` added the additive
#: ``task_runs.question_message_id`` answer watermark (#625) and the
#: ``chatter_dedupe`` idempotency table (#631); ``4`` added the nullable
#: ``task_runs.run_summary`` column holding the machine-derived run narrative
#: (#626). Provisioning reads this marker to tell an out-of-date DB (needs the
#: rebuild :func:`migrate_schema`) from a current one (idempotent no-op), which
#: ``CREATE ... IF NOT EXISTS`` alone cannot — it would silently skip an
#: already-present table whose CHECK is behind. A STRICT table's CHECK cannot be
#: altered in place, so each bump rebuilds the affected tables.
SCHEMA_VERSION = 4


# Canonical schema for the central tracker DB — the ONE authoritative DDL, applied
# only by the host provisioning step (``scripts/init_tracker_db.py``, invoked by
# ``setup.sh`` / ``setup.ps1``) and by :func:`create_schema`, never on connection
# open (#369; see :class:`TrackerStateMissingError`). The stdlib-only init script
# embeds a verbatim copy of this DDL (it cannot import the SDK on the host); an SDK
# parity test asserts the two produce an identical ``sqlite_master`` so they never
# drift.
#
# It carries EVERY column and index the pre-#369 per-repo DBs accumulated across
# their migrations — ``task_runs.aborted_at`` (#356), ``events.external_id`` with
# its partial unique dedupe index (resync), ``idx_events_timestamp`` (#359), and
# the ``session_uploads`` ``task_id``/``started_at``/``ended_at`` orphan-discovery
# columns (#353). Every statement is ``IF NOT EXISTS`` so provisioning is
# idempotent. Sessions are still derived from ``events`` at query time (see
# ``_DERIVE_SESSIONS_SQL``); there is no materialized ``sessions`` table.
#
# The ``chatter_dedupe`` table (#631) is the idempotency store for chatter posts:
# one row per ``(task_id, dedupe_key)`` — enforced by its unique index, the same
# pattern as the ``events(external_id)`` dedupe index — mapping a caller-supplied
# key to the chatter message it first produced, so a retried ``task_note`` /
# ``task_question`` returns the existing message instead of double-posting. The
# additive ``task_runs.question_message_id`` column (#625) is the answer-detection
# watermark stamped by ``task_question``.
#
# All tables are ``STRICT`` with CHECK constraints (#452) so malformed data
# fails at WRITE time — ``json_valid`` guards the JSON columns (``events.task_ids``,
# ``task_runs.notes``) and ``datetime(...) IS NOT NULL`` guards every timestamp —
# instead of surfacing later as a ``json_each``/``julianday`` failure inside a
# reporting query. The ``task_runs.state`` CHECK admits the terminal ``CLOSED``
# state (#504) alongside the three live/paused ones. Neither STRICT nor a CHECK
# constraint can be altered in place, so an out-of-date DB is rebuilt by
# :func:`migrate_schema` — a pre-STRICT (schema-version 0) DB has every table
# rebuilt, and a STRICT-but-pre-CLOSED (version 1) DB has ``task_runs`` rebuilt to
# widen its CHECK. The :data:`SCHEMA_VERSION` marker in ``PRAGMA user_version``
# lets provisioning tell an out-of-date DB (needs the rebuild) from a current one
# (idempotent no-op) rather than relying on ``IF NOT EXISTS`` alone, which would
# silently skip an already-present table whose shape is behind.
SCHEMA_DDL = """
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


class SchemaMigrationError(RuntimeError):
    """Raised when the tracker DB cannot be rebuilt into the STRICT schema (#452).

    The rebuild ABORTS — leaving the existing database untouched — rather than
    silently dropping rows that would violate the new STRICT/CHECK constraints.
    The message lists every offending row (``table[key=…]: reason``) so the
    operator can fix or delete them and re-run provisioning. Abort-and-report is
    chosen over silent quarantine: the local DB is low-volume, so a human deciding
    what to do with a handful of corrupt rows is safer than dropping data that a
    background provisioning step never surfaces.
    """


# Tables rebuilt by the STRICT migration, in a fixed order (no foreign keys, so
# any order is correct; fixed only for deterministic abort output).
_MIGRATION_TABLES = (
    "task_runs",
    "settings",
    "events",
    "session_uploads",
    "chatter_dedupe",
)

# Uppercase substrings whose ABSENCE from a table's stored ``sqlite_master`` DDL
# means it predates a schema change and must be rebuilt from the canonical DDL.
# ``STRICT`` (#452) applies to every table; the ``CLOSED`` state (#504) and the
# ``question_message_id`` watermark column (#625) and the ``run_summary``
# narrative column (#626) only to ``task_runs``. A marker check is used rather
# than an exact-SQL compare because SQLite does not store the ``IF NOT EXISTS``
# / whitespace verbatim, but it does preserve keywords, CHECK literals, and
# column names — the exact tokens that change between schema versions. Extend a
# table's tuple when a future CHECK/shape change needs the same version-guarded
# rebuild.
_REQUIRED_TABLE_MARKERS = {
    "task_runs": ("STRICT", "CLOSED", "QUESTION_MESSAGE_ID", "RUN_SUMMARY"),
    "settings": ("STRICT",),
    "events": ("STRICT",),
    "session_uploads": ("STRICT",),
    "chatter_dedupe": ("STRICT",),
}

# Per-table write-validation predicates mirroring the SCHEMA_DDL CHECK clauses.
# Reused at migration time to PRE-FLIGHT existing rows and build a precise abort
# listing before any destructive rewrite. Each entry is (SQL predicate that a BAD
# row satisfies, human-readable reason). Keep in lockstep with the CHECKs above.
_ROW_VALIDATIONS = {
    "events": (
        ("datetime(timestamp) IS NULL", "invalid timestamp"),
        ("NOT json_valid(task_ids)", "invalid task_ids JSON"),
    ),
    "task_runs": (
        ("datetime(started_at) IS NULL", "invalid started_at"),
        ("stopped_at IS NOT NULL AND datetime(stopped_at) IS NULL", "invalid stopped_at"),
        ("aborted_at IS NOT NULL AND datetime(aborted_at) IS NULL", "invalid aborted_at"),
        ("NOT json_valid(notes)", "invalid notes JSON"),
        (
            "state NOT IN ('RUNNING', 'AWAITING_ANSWERS', 'STOPPED', 'CLOSED')",
            "invalid state",
        ),
    ),
    "session_uploads": (
        ("datetime(uploaded_at) IS NULL", "invalid uploaded_at"),
        ("started_at IS NOT NULL AND datetime(started_at) IS NULL", "invalid started_at"),
        ("ended_at IS NOT NULL AND datetime(ended_at) IS NULL", "invalid ended_at"),
    ),
    "chatter_dedupe": (
        ("datetime(created_at) IS NULL", "invalid created_at"),
    ),
}

# Column used to identify an offending row of each table in the abort message.
_ROW_KEY = {
    "events": "id",
    "task_runs": "id",
    "session_uploads": "session_key",
    "chatter_dedupe": "dedupe_key",
}


def _ddl_statements() -> list:
    """Split :data:`SCHEMA_DDL` into its individual CREATE statements.

    Every statement is cleanly ``;``-terminated and none embeds a ``;`` (no string
    literal or CHECK clause does), so a plain split is exact. The migration rebuilds
    one table at a time from these — the SAME source a fresh provision uses — so a
    rebuilt schema can never drift from :data:`SCHEMA_DDL`.
    """
    return [stmt.strip() for stmt in SCHEMA_DDL.split(";") if stmt.strip()]


def _schema_by_table() -> dict:
    """Map each table name to its ``(CREATE TABLE, [CREATE INDEX, ...])`` DDL."""
    tables: dict = {}
    indexes = []
    for stmt in _ddl_statements():
        if stmt.upper().startswith("CREATE TABLE"):
            name = stmt.split("(", 1)[0].replace("IF NOT EXISTS", "").split()[-1]
            tables[name] = (stmt, [])
        else:
            target = stmt[stmt.upper().index(" ON ") + 4 :].split("(")[0].strip().split()[0]
            indexes.append((target, stmt))
    for target, stmt in indexes:
        tables[target][1].append(stmt)
    return tables


def _invalid_rows(conn: sqlite3.Connection, table: str) -> list:
    """Return a ``table[key=…]: reason`` line for every row failing validation."""
    checks = _ROW_VALIDATIONS.get(table)
    if not checks:
        return []
    key = _ROW_KEY[table]
    where = " OR ".join(f"({pred})" for pred, _ in checks)
    reason = " ".join(f"WHEN {pred} THEN {label!r}" for pred, label in checks)
    rows = conn.execute(
        f"SELECT {key}, CASE {reason} END FROM {table} WHERE {where}"
    ).fetchall()
    return [f"{table}[{key}={k}]: {label}" for k, label in rows]


def _stale_tables(conn: sqlite3.Connection) -> list:
    """Return the existing tables whose DDL predates the current schema.

    A table is stale when its stored DDL is missing any of its
    :data:`_REQUIRED_TABLE_MARKERS` — a pre-STRICT (#452) shape or a pre-CLOSED
    (#504) ``task_runs`` CHECK — both repaired by the same canonical rebuild.
    Missing tables (a fresh DB) are skipped. Ordered by :data:`_MIGRATION_TABLES`
    for deterministic abort output.
    """
    stale = []
    for table in _MIGRATION_TABLES:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if row is None:
            continue
        sql = row[0].upper()
        if any(marker not in sql for marker in _REQUIRED_TABLE_MARKERS[table]):
            stale.append(table)
    return stale


def _table_columns(conn: sqlite3.Connection, table: str) -> list:
    """Return ``table``'s column names in declaration order."""
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def _rebuild_table(
    conn: sqlite3.Connection, table: str, create_sql: str, index_sqls: list
) -> None:
    """Rebuild one table into its current canonical form, preserving rows and ids.

    The canonical SQLite table rebuild: rename the old table aside, create the new
    typed table from the SAME DDL a fresh provision uses, copy every row over the
    columns the two shapes SHARE (named explicitly, so an additive bump such as
    the #625 ``question_message_id`` column simply lands NULL for pre-existing
    rows instead of breaking a positional ``SELECT *`` copy — ids and all shared
    values carry over), drop the old table (freeing its index names), then
    recreate the indexes. The caller has already pre-flighted every row, so the
    copy cannot fail on data.
    """
    old = f"{table}__pre_strict"
    conn.execute(f'ALTER TABLE "{table}" RENAME TO "{old}"')
    conn.execute(create_sql)
    new_columns = _table_columns(conn, table)
    shared = ", ".join(
        f'"{column}"' for column in _table_columns(conn, old) if column in new_columns
    )
    conn.execute(f'INSERT INTO "{table}" ({shared}) SELECT {shared} FROM "{old}"')
    conn.execute(f'DROP TABLE "{old}"')
    for index_sql in index_sqls:
        conn.execute(index_sql)


def migrate_schema(conn: sqlite3.Connection) -> None:
    """Rebuild any out-of-date tables into the current STRICT typed schema.

    Repairs every migration the tracker DB has needed: a pre-#452 non-STRICT DB
    (every table rebuilt into STRICT form), a pre-#504 DB whose ``task_runs``
    CHECK lacks the ``CLOSED`` state (that table rebuilt to widen the CHECK), a
    pre-#625 DB whose ``task_runs`` lacks the ``question_message_id`` watermark
    column, and a pre-#626 DB whose ``task_runs`` lacks the ``run_summary``
    narrative column (that table rebuilt; existing rows land NULL for the added
    columns) — see :data:`_REQUIRED_TABLE_MARKERS`. The ``chatter_dedupe`` table
    (#631) needs no rebuild: absent tables are created by the ``IF NOT EXISTS``
    DDL that follows in :func:`create_schema`. A no-op when the DB is already at
    :data:`SCHEMA_VERSION` or holds no out-of-date tables (a fresh DB — its tables
    are created current directly by :func:`create_schema`). Otherwise every
    offending row is listed and the migration ABORTS with
    :class:`SchemaMigrationError` BEFORE any destructive rewrite, so a DB that
    cannot be cleanly migrated is left exactly as it was. The rebuild itself runs
    inside one transaction and is all-or-nothing.
    """
    if conn.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
        return
    stale = _stale_tables(conn)
    if not stale:
        return
    problems = [line for table in stale for line in _invalid_rows(conn, table)]
    if problems:
        raise SchemaMigrationError(
            "Cannot migrate tracker.db to the STRICT schema: "
            f"{len(problems)} row(s) fail the new write-time validation and would "
            "be lost. Fix or delete them, then re-run provisioning:\n"
            + "\n".join(problems)
        )
    schema = _schema_by_table()
    conn.commit()
    conn.execute("BEGIN")
    try:
        for table in stale:
            create_sql, index_sqls = schema[table]
            _rebuild_table(conn, table, create_sql, index_sqls)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def create_schema(conn: sqlite3.Connection) -> None:
    """Bring ``conn`` to the current STRICT schema (host provisioning / tests only).

    The ONLY sanctioned way to bring a tracker DB up to schema, called from exactly
    two places: the host-side ``scripts/init_tracker_db.py`` (which embeds an
    identical DDL copy + migration for stdlib-only host use) and the SDK test
    suite's shared fixture. It is NEVER called on connection open — the SDK consumes
    a host-provisioned DB and refuses to self-create one (#369; see
    :class:`TrackerStateMissingError`).

    Idempotent and version-aware: it first rebuilds any pre-STRICT tables
    (:func:`migrate_schema`), then applies the ``IF NOT EXISTS`` DDL to create any
    missing tables on a fresh DB, then stamps :data:`SCHEMA_VERSION` into
    ``PRAGMA user_version`` so a subsequent run is a fast no-op.
    """
    migrate_schema(conn)
    conn.executescript(SCHEMA_DDL)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


# Development-family sources: ``commit``, ``agent``, ``chatter`` resync events,
# the ``calendar`` meeting ticks and ``email`` sent-mail point events (#370), plus
# the open-ended ``claude:<HookName>`` family (EventType.CLAUDE_HOOK). Calendar
# ticks are synthetic point events emitted 5 min apart across a meeting so the
# UNCHANGED gap derivation reconstructs the meeting as one session (#370); a sent
# email is a lone point event that picks up the #355 minimum like a commit. A
# derived group containing ANY development-family event is labeled "Development".
_DEVELOPMENT_SOURCE_PREDICATE = (
    "(source IN ('commit', 'agent', 'chatter', 'calendar', 'email') "
    "OR source LIKE 'claude:%')"
)

# Review-family sources (#378 item 6): submitted PR ``review`` passes, authored
# PR/issue ``comment`` events (``gh:comment:<id>``), and PR-opening ``pr_opened``
# events (``gh:pr:<n>:opened``, #656) — opening a PR is billable review-family
# work, floored like a lone review. Review activity is *bursty* — e.g. one pass
# of 33 inline comments, or a stack of PRs opened minutes apart — which the
# Python ETL's ``FixedDurationStrategy`` over-bills (15 min × 33 = 8.25 h) and a
# lone long pass under-bills; gap-windowing models both correctly (the burst
# becomes one session, a lone review/opened PR a single-event session that
# floors to the #355 minimum). A group with ONLY review-family events is labeled
# "Review" (development wins a mixed group). ``merge`` stays deliberately OUT of
# the windowed derivation — it is a point-in-time release marker kept for audit,
# not a work span — and remains the only excluded ingested source.
_REVIEW_SOURCE_PREDICATE = "source IN ('review', 'comment', 'pr_opened')"

# Sources whose events participate in gap-based sessionization: the union of the
# development and review families. ``merge`` is the only ingested source excluded.
_SESSION_SOURCE_PREDICATE = (
    f"({_DEVELOPMENT_SOURCE_PREDICATE} OR {_REVIEW_SOURCE_PREDICATE})"
)


# CTE that reproduces the legacy gap-based sessionization directly over ``events``
# at query time. The inactivity gap is bound at execution (SQLite views cannot
# take a parameter), so zero materialization/staleness exists for any producer.
# The consecutive-event delta is ``ROUND``ed to whole seconds before the gap
# comparison: ``julianday`` arithmetic carries a float epsilon that would
# otherwise make two events *exactly* ``gap_secs`` apart read as > the gap and
# spuriously split. Event timestamps are second-resolution for sessionization, so
# rounding is exact at the boundary and matches the legacy ``total_seconds()`` cut.
#
# Partitioning (#352): sessions partition by ``task_key`` ALONE — the repo is no
# longer a partition key. Agent/hook/MCP events carry ``repo=""`` while resync
# commit/chatter events carry the real ``owner/repo`` label; keying on repo split
# one task's span into two parallel lanes and billed it twice. Repo survives as
# display metadata: ``COALESCE(MAX(NULLIF(repo,'')), :agentless)`` per group
# prefers the real label and falls back to an absent repo (#508). Distinct
# *tasks* still partition separately, so genuine concurrent tasks bill in parallel.
#
# Window prefilter (#359): the ``base`` CTE bounds ``timestamp`` to the queried
# ``[start, end]`` widened by :func:`_derivation_margin` each side, so a TUI
# refresh scans a slice via ``idx_events_timestamp`` instead of sessionizing every
# event ever recorded. The margin (``max(gap_secs, 1 day)``) is wide enough that a
# session merely straddling the queried window still pulls in its neighbours whole;
# a session whose total span exceeds the margin can in theory be clipped at the
# far edge (documented, accepted tradeoff — no fixed margin bounds a gap chain).
#
# Fan-out (#362): a multi-task event (``--attach-active-run`` attaches EVERY
# active run's task id, so a single hook event can carry ``task_ids=[t1, t2]``)
# is fanned out over its task ids with ``json_each``: it yields one ``base`` row
# per task id and thus extends BOTH tasks' sessions instead of only the first.
# ``events.id`` is qualified because ``json_each`` also exposes an ``id`` column.
# ``DISTINCT`` collapses a task id that appears twice in one array so a duplicated
# id can never double-count an event within its own session. One event id can now
# anchor two tasks' sessions; keys stay distinct because the session key is
# ``task|min_event_id`` (task-scoped).
#
# Category (#378 item 6): review-family sources (``review``/``comment``) now form
# WINDOWED sessions alongside the development family, so the derivation must label
# each session so the TUI can distinguish "Review" from "Development". Each ``base``
# row carries an ``is_dev`` flag (1 for a development-family source, 0 for a
# review-family one); ``MAX(is_dev)`` per group is the label decision — a group with
# ANY development-family event is "Development" (development wins a mixed task's
# label), a group of purely review-family events is "Review". The flag is derived
# from ``source`` alone, so it does not perturb the ``DISTINCT`` (one id → one row).
#
# Task attribution (#409): the ``json_array_length(events.task_ids) > 0`` filter
# below — with ``COALESCE(NULLIF(value, ''), 'UNKNOWN')`` bucketing empty ids — is
# the CANONICAL attribution predicate. The diagnostic gap-sweep mirrors the same
# "non-empty ``task_ids``" rule in ``sessionization/transform.py``
# (``billable_events``); parity is pinned by ``test_sessionization/test_parity.py``.
_DERIVE_SESSIONS_SQL = f"""
WITH base AS (
    SELECT DISTINCT
           events.id AS id, events.timestamp AS timestamp,
           events.pr_num AS pr_num, events.repo AS repo,
           julianday(events.timestamp) AS jd,
           CASE WHEN {_DEVELOPMENT_SOURCE_PREDICATE} THEN 1 ELSE 0 END AS is_dev,
           COALESCE(NULLIF(task_each.value, ''), 'UNKNOWN') AS task_key
    FROM events, json_each(events.task_ids) AS task_each
    WHERE {_SESSION_SOURCE_PREDICATE}
      AND json_array_length(events.task_ids) > 0
      AND events.timestamp >= :wstart
      AND events.timestamp <= :wend
),
marked AS (
    SELECT *,
           CASE WHEN LAG(jd) OVER w IS NULL
                  OR ROUND((jd - LAG(jd) OVER w) * 86400.0) > :gap_secs
                THEN 1 ELSE 0 END AS is_start
    FROM base
    WINDOW w AS (PARTITION BY task_key ORDER BY jd, id)
),
numbered AS (
    SELECT *,
           SUM(is_start) OVER (PARTITION BY task_key
                               ORDER BY jd, id ROWS UNBOUNDED PRECEDING) AS session_num
    FROM marked
)
SELECT task_key,
       COALESCE(MAX(NULLIF(repo, '')), :agentless) AS repo_display,
       MIN(id)            AS session_key_id,
       MIN(timestamp)     AS started_at,
       MAX(timestamp)     AS ended_at,
       MAX(pr_num)        AS pr_num,
       MAX(is_dev)        AS has_dev,
       json_group_array(id) AS event_ids
FROM numbered
GROUP BY task_key, session_num
HAVING started_at <= :end AND ended_at >= :start
{{extra}}
ORDER BY started_at
"""


def _resolve_state_root() -> Path:
    """Resolve the tracker state root, honoring the same env overrides.

    Precedence: ``ODOO_TASK_TRACKER_DIR`` (highest) then the XDG-aware
    :func:`_default_root`. This is the single resolver both self-resolved
    ``LocalStateClient`` construction and cross-project discovery share, so the
    directory a DB is written under is exactly the one discovery scans.
    """
    override = os.environ.get("ODOO_TASK_TRACKER_DIR")
    return Path(override) if override else _default_root()


def _derive_repo_label(remote_url: str) -> str:
    """Return the ``owner/repo`` label for a git remote URL.

    Strips a trailing ``.git`` and keeps the last two path segments so both ssh
    (``git@github.com:owner/repo.git``) and https
    (``https://github.com/owner/repo.git``) forms collapse to ``owner/repo``.
    Falls back to the cleaned URL when it has fewer than two segments.
    """
    cleaned = remote_url.strip()
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    # Treat ``:`` (scp-like ssh separator, scheme ``://``) as a path separator so
    # both URL shapes split into the same segment list.
    segments = [seg for seg in cleaned.replace(":", "/").split("/") if seg]
    if len(segments) >= 2:
        return "/".join(segments[-2:])
    return cleaned or "(unknown)"


def tracker_db_path(root: Optional[Path] = None) -> Path:
    """Return the path to the single central tracker DB (#369).

    ``<state-root>/tracker.db``, where the state root is ``root`` when given, else
    the env-aware :func:`_resolve_state_root` (``ODOO_TASK_TRACKER_DIR`` → XDG).
    No git remote is consulted and no directory is created (#369): the location is
    fixed and the DB's existence is the host's responsibility, not the SDK's.
    """
    base = Path(root) if root is not None else _resolve_state_root()
    return base / TRACKER_DB_FILENAME


def assert_tracker_db_present(path: Optional[Path] = None) -> None:
    """Raise :class:`TrackerStateMissingError` when the tracker DB is absent.

    The DB is host-provisioned and the SDK NEVER creates it (#369), so its
    absence is a setup failure with a fixed remedy rather than something to
    recover from. This is the single definition of that check and of its
    message: :meth:`LocalStateClient._raw_connect` calls it just before opening a
    connection, and :func:`~odoo_sdk.utilities.env.assert_sdk_configured` calls
    it up front so a command fails on its precondition rather than mid-body
    (#642).

    ``path`` defaults to :func:`tracker_db_path`, which creates no directories.
    """
    db_path = Path(path) if path is not None else tracker_db_path()
    if not db_path.exists():
        raise TrackerStateMissingError(
            f"No tracker database at {db_path}. This database is "
            "provisioned on the host and bind-mounted into the container; it is "
            "not created automatically. Run setup.sh on the host, then rebuild "
            "the container."
        )


def current_repo_label() -> str:
    """Return the normalized ``owner/repo`` label for the cwd's git remote, or ''.

    Best-effort display metadata for events written from a working tree (#369):
    the repo no longer selects the database (there is one central DB), so a
    non-git cwd or a missing remote is not an error — it simply yields ``""``,
    which sessionizes as a repo-less session. ssh and https clones of one
    repo converge because :func:`_derive_repo_label` normalizes both URL shapes
    to the same ``owner/repo``.
    """
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return ""
    label = _derive_repo_label(result.stdout.strip())
    return "" if label == "(unknown)" else label


# Columns selected for every task_run read, in _parse_run order.
_TASK_RUN_COLUMNS = (
    "id, task_id, task_name, project_id, project_name, state, "
    "started_at, stopped_at, timesheet_id, notes, aborted_at, "
    "question_message_id, run_summary"
)

# The two live/paused states an "active" run may be in. "Active" is the live
# pair RUNNING/AWAITING_ANSWERS only; STOPPED (resumable, #504) and CLOSED
# (terminal) are deliberately excluded, so every lookup and CAS guard built on
# this literal stays the single-live-run guard the FSM invariants rely on.
_ACTIVE_STATES_SQL = "('RUNNING', 'AWAITING_ANSWERS')"

# WHERE clause selecting a task's single active run (see _ACTIVE_STATES_SQL).
_ACTIVE_RUN_WHERE = f"WHERE task_id = ? AND state IN {_ACTIVE_STATES_SQL}"

# WHERE clause selecting a task's most recent reopenable STOPPED run (#504):
# aborted stopped runs (``aborted_at`` stamped) are voided from billing and NOT
# resumable; CLOSED runs are terminal and never match.
_RESUMABLE_RUN_WHERE = (
    "WHERE task_id = ? AND state = 'STOPPED' AND aborted_at IS NULL "
    "ORDER BY started_at DESC, id DESC LIMIT 1"
)


def _no_active_session_error(task_id: int) -> TaskNotRunningError:
    """Return THE no-active-session error — one implementation, one message.

    Every surface that guards on "an active session must exist" — the command
    layer's ``require_active_run``, :meth:`LocalStateClient.require_active_run`,
    and the CAS write paths below — raises this same error with this same
    message (#627), so callers and tests never see divergent wordings for the
    same condition.
    """
    return TaskNotRunningError(f"No active session for task {task_id}.")


def _fetch_run(
    conn: sqlite3.Connection, where: str, params: tuple
) -> Optional[TaskRun]:
    """Return the single matching task_run using an EXISTING connection, or None.

    The in-transaction sibling of :meth:`LocalStateClient._select_run`: write
    paths that must read-decide-write atomically (#628) fetch through this on
    the one connection holding their ``BEGIN IMMEDIATE`` transaction, so the
    row they decide on cannot change under them.
    """
    row = conn.execute(
        f"SELECT {_TASK_RUN_COLUMNS} FROM task_runs {where}", params
    ).fetchone()
    return _parse_run(row) if row else None


def _cas_update(
    conn: sqlite3.Connection, sql: str, params: tuple, error: Exception
) -> None:
    """Execute a compare-and-swap UPDATE; raise ``error`` when no row matched.

    State transitions guard their UPDATE with ``WHERE id = ? AND state IN
    (...)`` (#628): a row already moved out of the admissible states by a
    concurrent writer matches nothing, and the rowcount-0 outcome maps to the
    same state error the pre-check raises — never a new exception type — so a
    lost race is a deterministic, familiar failure rather than a silent
    overwrite. Under :meth:`LocalStateClient._write_txn`'s ``BEGIN IMMEDIATE``
    the in-transaction pre-check already holds, making this the belt to that
    suspender; it fires on its own when a caller CASes without a pre-read.
    """
    if conn.execute(sql, params).rowcount == 0:
        raise error


def _parse_run(row: tuple) -> TaskRun:
    (
        id_,
        task_id,
        task_name,
        project_id,
        project_name,
        state,
        started_at,
        stopped_at,
        timesheet_id,
        notes_json,
        aborted_at,
        question_message_id,
        run_summary,
    ) = row
    return TaskRun(
        id=id_,
        task_id=task_id,
        task_name=task_name,
        project_id=project_id,
        project_name=project_name,
        state=TaskState(state),
        started_at=datetime.fromisoformat(started_at),
        stopped_at=datetime.fromisoformat(stopped_at) if stopped_at else None,
        timesheet_id=timesheet_id,
        notes=json.loads(notes_json),
        aborted_at=datetime.fromisoformat(aborted_at) if aborted_at else None,
        question_message_id=question_message_id,
        run_summary=run_summary,
    )


# Columns selected for every event read, in EventRecord field order.
_EVENT_COLUMNS = (
    "id, source, timestamp, task_ids, repo, pr_num, branch, subject, payload, "
    "external_id"
)


def _normalize_utc_isoformat(ts: datetime) -> str:
    """Return ``ts`` as a uniform UTC isoformat string, used for stored values and bounds.

    The SQL-derived read path compares ``MIN/MAX(timestamp)`` and query bounds as
    *strings*, which is only correct when every value shares one UTC offset. An aware
    timestamp is converted to UTC; a naive one — a stored value, the TUI's
    ``datetime.combine(date, time.min)`` window edge, or the query layer's
    ``datetime.min``/``datetime.max`` sentinel — is treated as already-UTC and
    stamped with ``+00:00``, so all rows and bounds sort and compare uniformly (the
    naive sentinels still sort past every real row).
    """
    return as_utc(ts).isoformat()


def _derivation_margin(gap_secs: int) -> timedelta:
    """Return how far the derivation prefilter widens the queried window (#359).

    A gap-based session is a chain of events each at most ``gap_secs`` apart, so
    no fixed margin can guarantee a session's *whole* span is captured. We widen
    by ``max(gap_secs, 1 day)`` each side: one full inactivity gap keeps an event
    sitting exactly on the boundary chained to its neighbour, and one day covers a
    typical work session that merely straddles the window. A session whose total
    span exceeds this margin can be clipped at its far edge — an accepted tradeoff
    for not sessionizing the entire events table on every refresh.
    """
    return timedelta(seconds=max(gap_secs, 86_400))


def _widen(ts: datetime, delta: timedelta) -> datetime:
    """Return ``ts + delta``, clamped to ``datetime.min``/``max`` on over/underflow.

    The query layer passes ``datetime.min``/``datetime.max`` for an unbounded edge,
    and shifting those overflows; clamping by the sign of ``delta`` keeps the widened
    bound a valid, still-past-everything sentinel.
    """
    try:
        return ts + delta
    except OverflowError:
        return datetime.min if delta < timedelta(0) else datetime.max


def _window_where(
    start: Optional[datetime],
    end: Optional[datetime],
    extra_clauses: Sequence[str] = (),
) -> tuple[str, list[str]]:
    """Build the optional ``[start, end)`` timestamp WHERE clause and its params.

    Bounds are half-open (``>= start``, ``< end``) and normalized to the uniform
    stored-timestamp string form. ``extra_clauses`` are ANDed ahead of the bounds
    (e.g. an unattributed-events filter that always yields a WHERE).
    """
    clauses = list(extra_clauses)
    params: list[str] = []
    if start is not None:
        clauses.append("timestamp >= ?")
        params.append(_normalize_utc_isoformat(start))
    if end is not None:
        clauses.append("timestamp < ?")
        params.append(_normalize_utc_isoformat(end))
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def _parse_event(row: tuple) -> EventRecord:
    (id_, source, ts, task_ids, repo, pr_num, branch, subject, payload, ext_id) = row
    return EventRecord(
        id=id_,
        source=source,
        timestamp=datetime.fromisoformat(ts),
        task_ids=json.loads(task_ids),
        repo=repo,
        pr_num=pr_num,
        branch=branch,
        subject=subject,
        payload=json.loads(payload) if payload else None,
        external_id=ext_id,
    )


def _parse_session_upload(row: tuple) -> dict:
    """Shape a ``session_uploads`` row into the accessor's dict.

    ``task_id``/``started_at``/``ended_at`` are ``None`` for legacy rows written
    before #353 added the orphan-discovery columns.
    """
    return {
        "session_key": row[0],
        "timesheet_id": row[1],
        "hours": row[2],
        "uploaded_at": row[3],
        "task_id": row[4],
        "started_at": row[5],
        "ended_at": row[6],
    }


def _parse_derived_window(row: tuple) -> SessionWindow:
    """Build a :class:`SessionWindow` from a ``derive_sessions_overlapping`` row.

    The derived row carries ``(task_key, repo_display, session_key_id,
    started_at, ended_at, pr_num, has_dev, event_ids)`` where ``event_ids`` is a
    JSON array. ``repo_display`` is display-only metadata (#352): the group's real
    ``owner/repo`` label when any event carried one, else :data:`AGENTLESS_REPO`
    (the empty string) — render it through :func:`format_repo_label` (#508).
    ``id`` is the session's minimum event id (stable under append-only tail writes).

    ``has_dev`` labels the window (#378 item 6): ``1`` when the group holds any
    development-family event → ``development`` / ``Development`` (development wins
    a mixed task's label); ``0`` when the group is purely review-family
    (``review``/``comment``) → ``review`` / ``Review`` so the TUI can badge it.

    ``event_ids`` is sorted ascending in Python: ``json_group_array`` has no
    order guarantee (SQLite < 3.44 rejects an aggregate ``ORDER BY``), so sorting
    by id — which is monotonic with insertion — gives a deterministic order for
    the bulk event fetch instead of relying on the group-scan order.
    """
    (task_key, repo_display, session_key_id, started, ended, pr_num, has_dev,
     event_ids_json) = row
    strategy_name, category = ("development", "Development") if has_dev else (
        "review", "Review"
    )
    return SessionWindow(
        id=session_key_id,
        task_id=task_key,
        repo=repo_display,
        started_at=datetime.fromisoformat(started),
        ended_at=datetime.fromisoformat(ended),
        strategy_name=strategy_name,
        category=category,
        pr_num=pr_num,
        event_ids=tuple(sorted(json.loads(event_ids_json))),
    )


class LocalStateClient:
    """SQLite-backed state store for task tracking sessions."""

    def __init__(self, db_path: Optional[Path] = None):
        """Bind to a tracker DB path WITHOUT touching the filesystem (#369).

        Construction is inert — no git remote, directory, or schema work. A missing
        DB is not detected until the first :meth:`_connect`, which raises
        :class:`TrackerStateMissingError`. ``db_path`` defaults to the single central
        :func:`tracker_db_path`; tests and callers pass an explicit path.
        """
        self._db_path = Path(db_path) if db_path is not None else tracker_db_path()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection inside a transaction, then checkpoint and close it.

        ``with self._connect() as conn:`` keeps the stdlib ``with connection:``
        semantics every call site relies on — commit on success, rollback on
        exception — because the inner ``with conn:`` is still what brackets the
        body. What the wrapper adds is the *close*, which the bare-connection form
        never did: ``with connection:`` is a transaction manager, not a closing
        one, so connections were left for refcounting to collect (#495).

        Closing matters because the WAL sidecars (``-wal``/``-shm``) are only
        removed when the last connection closes cleanly, so they used to persist
        at rest — and a backup that copied ``tracker.db`` alone silently dropped
        every transaction still sitting in the WAL. ``wal_checkpoint(TRUNCATE)``
        folds the WAL back into the main file first, so the DB is a single file at
        rest while keeping WAL's reader/writer concurrency while it is open.

        The checkpoint is best-effort: it needs a lock no concurrent writer holds,
        and the commit has already happened by the time it runs, so a busy
        database is swallowed exactly as the :meth:`vacuum` reclaim is.
        A maintenance copy taken while writers are live must therefore still not
        assume single-file state: use ``VACUUM INTO '<dest>'`` or ``sqlite3
        .backup`` (both WAL-aware) rather than copying ``tracker.db`` on its own.

        WAL itself stays on — it is load-bearing for the cross-container writers
        documented on :meth:`_raw_connect`, so ``journal_mode=DELETE`` is NOT the
        fix here (#495).
        """
        conn = self._raw_connect()
        try:
            with conn:
                yield conn
        finally:
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.OperationalError:  # pragma: no cover - lock contention
                pass
            finally:
                conn.close()

    def _raw_connect(self) -> sqlite3.Connection:
        """Open one configured connection; the caller MUST close it.

        Use :meth:`_connect` instead — it is the only sanctioned entry point and
        the only one that closes. This raw form exists so the wrapper (and the
        wrapper alone) owns the connection lifecycle.
        """
        # The DB is host-provisioned; the SDK NEVER creates it (#369; see
        # :class:`TrackerStateMissingError`). ``mode=rw`` raises rather than creating
        # a missing file; the explicit existence check turns that into the single
        # named error every entry point surfaces — shared with the up-front
        # capability guard so both report it identically (#642).
        assert_tracker_db_present(self._db_path)
        conn = sqlite3.connect(f"file:{self._db_path}?mode=rw", uri=True)
        # WAL lets a writer and readers proceed concurrently, and a 2s busy
        # timeout makes a second writer wait for the lock instead of failing
        # instantly with "database is locked". With one central DB now taking
        # cross-container writers (the claude-event-hook shim, MCP
        # _emit_tool_event, the TUI), these are load-bearing rather than optional
        # (#357). WAL is a persistent property of the DB file (set by the host
        # provisioning step and re-asserted here); the busy timeout is
        # per-connection and so must be set on every connect. WAL works on a
        # Docker bind mount on Linux (Docker Desktop's gRPC-FUSE share is the only
        # environment where it can misbehave — not our containers-on-Linux case).
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=2000")
        # Foreign-key enforcement is intentionally left at SQLite's default (off).
        # The current schema declares no foreign keys.
        return conn

    @contextmanager
    def _write_txn(self) -> Iterator[sqlite3.Connection]:
        """One immediate-mode write transaction on one connection (#628).

        The write layer's entry point: every logical mutation (create, state
        transition, note append) runs its checks AND its writes inside a single
        ``BEGIN IMMEDIATE`` transaction on a single connection. ``IMMEDIATE``
        acquires SQLite's write lock up front, so with the documented concurrent
        writers (the claude-event-hook shim, MCP ``_emit_tool_event``, the TUI —
        see :meth:`_raw_connect`) a read-decide-write sequence in the body can
        never interleave with another writer: whatever the body reads still
        holds when its UPDATE lands. A concurrent writer waits on the
        ``busy_timeout`` rather than seeing intermediate state. Commit on clean
        exit, rollback on exception, and close-with-checkpoint all come from
        :meth:`_connect`.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            yield conn

    def _select_runs(self, where: str, params: tuple = ()) -> list[TaskRun]:
        """Run ``SELECT {_TASK_RUN_COLUMNS} FROM task_runs {where}`` and parse rows."""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_TASK_RUN_COLUMNS} FROM task_runs {where}", params
            ).fetchall()
        return [_parse_run(row) for row in rows]

    def _select_run(self, where: str, params: tuple) -> Optional[TaskRun]:
        """Return the single matching task_run, or None."""
        with self._connect() as conn:
            return _fetch_run(conn, where, params)

    def require_active_run(self, task_id: int) -> TaskRun:
        """Return the active run for ``task_id`` or raise :class:`TaskNotRunningError`.

        The ONE active-session guard (#627): the command layer's
        ``require_active_run`` delegates here, so every surface (MCP tools, CLI,
        library callers) raises the same error with the same message.
        """
        run = self.get_active_run(task_id)
        if run is None:
            raise _no_active_session_error(task_id)
        return run

    def get_active_run(self, task_id: int) -> Optional[TaskRun]:
        # See _ACTIVE_STATES_SQL for why STOPPED/CLOSED are excluded.
        return self._select_run(_ACTIVE_RUN_WHERE, (task_id,))

    def get_resumable_run(self, task_id: int) -> Optional[TaskRun]:
        """Return the most recent reopenable STOPPED run for a task, or None (#504).

        A STOPPED run is resumable: :meth:`transition_to_running` reopens it and
        ``start_task`` auto-resumes it instead of inserting a second row, so one
        continuous effort stays one run. An *aborted* STOPPED run (``aborted_at``
        stamped — deliberately voided from billing by abort/reap) is NOT resumable:
        reopening it would resurrect time a human or the reaper chose to discard,
        so a fresh ``start_task`` opens a new run instead. CLOSED runs are terminal
        and never match. The newest qualifying run is returned when a task has
        several stopped runs on record.
        """
        return self._select_run(_RESUMABLE_RUN_WHERE, (task_id,))

    def get_run_by_id(self, run_id: int) -> Optional[TaskRun]:
        return self._select_run("WHERE id = ?", (run_id,))

    def get_all_active_runs(self) -> list[TaskRun]:
        return self._select_runs(
            "WHERE state IN ('RUNNING', 'AWAITING_ANSWERS') ORDER BY started_at"
        )

    def get_all_runs(self) -> list[TaskRun]:
        # Terminal CLOSED runs are hidden from the default run listing (#504): the
        # CLI ``report``/``list`` tables and the TUI run count read through here,
        # so a closed run drops out of every default surface. Targeted lookups
        # (:meth:`get_run_by_id`) still see it.
        return self._select_runs("WHERE state != 'CLOSED' ORDER BY started_at")

    def get_stopped_runs_with_timesheet(self) -> list[TaskRun]:
        return self._select_runs(
            "WHERE state = 'STOPPED' AND timesheet_id IS NOT NULL ORDER BY started_at"
        )

    def create_run(
        self,
        task_id: int,
        task_name: str,
        project_id: int,
        project_name: str,
        timesheet_id: Optional[int] = None,
    ) -> TaskRun:
        started_at = datetime.now(timezone.utc).isoformat()
        # Check and insert share one BEGIN IMMEDIATE transaction (#628): a
        # concurrent create for the same task waits on the write lock and then
        # sees this row, so exactly one create wins the single-active-run
        # invariant instead of two check-then-insert sequences both passing.
        with self._write_txn() as conn:
            existing = _fetch_run(conn, _ACTIVE_RUN_WHERE, (task_id,))
            if existing is not None:
                raise TaskAlreadyRunningError(
                    f"Task {task_id!r} ({task_name!r}) already has an active session "
                    f"(id={existing.id}, state={existing.state.value})."
                )
            cursor = conn.execute(
                "INSERT INTO task_runs (task_id, task_name, project_id, project_name, "
                "state, started_at, timesheet_id, notes) VALUES (?, ?, ?, ?, 'RUNNING', ?, ?, '[]')",
                (task_id, task_name, project_id, project_name, started_at, timesheet_id),
            )
            return _fetch_run(  # type: ignore[return-value]
                conn, "WHERE id = ?", (cursor.lastrowid,)
            )

    def transition_to_awaiting(self, task_id: int) -> TaskRun:
        with self._write_txn() as conn:
            run = _fetch_run(conn, _ACTIVE_RUN_WHERE, (task_id,))
            if run is None:
                raise _no_active_session_error(task_id)
            # The active lookup only returns RUNNING/AWAITING_ANSWERS rows —
            # STOPPED (resumable) and CLOSED (terminal) are excluded — so every
            # run read above may transition to AWAITING_ANSWERS, and under this
            # transaction's write lock no other writer can move it first.
            _cas_update(
                conn,
                "UPDATE task_runs SET state = 'AWAITING_ANSWERS' "
                f"WHERE id = ? AND state IN {_ACTIVE_STATES_SQL}",
                (run.id,),
                _no_active_session_error(task_id),
            )
            return _fetch_run(  # type: ignore[return-value]
                conn, "WHERE id = ?", (run.id,)
            )

    def transition_to_running(self, task_id: int) -> TaskRun:
        """Ensure the task's run is RUNNING, resuming a paused one (#504, #621).

        Two predecessors resume: an ``AWAITING_ANSWERS`` run (a question was
        answered) and a ``STOPPED`` run (work continues after a stop). A stopped
        run is reopened IN PLACE — its ``started_at`` is preserved and its
        ``stopped_at`` cleared, so one continuous effort stays one run instead of
        splitting into a second row. An already-``RUNNING`` run is a NO-OP
        success (#621): the run is returned unchanged, so idempotent automation
        (``start_task``/``resume_task`` retries, racing resumers) never errors on
        "already running". A task with no resumable run at all raises
        :class:`TaskNotRunningError`. A ``CLOSED`` run is terminal and an
        aborted stopped run is voided, so neither is returned by the lookups below.
        """
        with self._write_txn() as conn:
            run = _fetch_run(conn, _ACTIVE_RUN_WHERE, (task_id,))
            if run is not None and run.state == TaskState.RUNNING:
                # No-op path (#621): RUNNING → RUNNING is success, not an error.
                return run
            if run is None:
                run = _fetch_run(conn, _RESUMABLE_RUN_WHERE, (task_id,))
            if run is None:
                raise TaskNotRunningError(
                    f"No resumable session found for task {task_id}."
                )
            # Defense in depth: under BEGIN IMMEDIATE the state read above still
            # holds, so this CAS cannot lose; a rowcount of 0 would mean the
            # transaction contract itself broke.
            _cas_update(
                conn,
                "UPDATE task_runs SET state = 'RUNNING', stopped_at = NULL "
                "WHERE id = ? AND state IN ('AWAITING_ANSWERS', 'STOPPED')",
                (run.id,),
                InvalidStateTransitionError(
                    f"Cannot resume task {task_id}: its state changed mid-transition."
                ),
            )
            return _fetch_run(  # type: ignore[return-value]
                conn, "WHERE id = ?", (run.id,)
            )

    def close_run(self, task_id: int) -> TaskRun:
        """Move a task's open run to the terminal ``CLOSED`` state (#504, CLI-only).

        Closes the task's live run (``RUNNING``/``AWAITING_ANSWERS``) or, when none
        is live, its most recent resumable ``STOPPED`` run. ``CLOSED`` is terminal:
        neither ``resume_task`` nor ``start_task``'s auto-resume reopens it, and it
        is hidden from :meth:`get_all_runs` and every active query. A live run's
        ``stopped_at`` is stamped now; an already-stopped run keeps its stamp.
        Invisible to MCP by design — reachable only from the CLI ``close`` command.

        :raises TaskNotRunningError: When the task has no live or resumable run.
        """
        with self._write_txn() as conn:
            run = _fetch_run(conn, _ACTIVE_RUN_WHERE, (task_id,))
            if run is None:
                run = _fetch_run(conn, _RESUMABLE_RUN_WHERE, (task_id,))
            if run is None:
                raise TaskNotRunningError(
                    f"No open session to close for task {task_id}."
                )
            stopped_at = (
                run.stopped_at.isoformat()
                if run.stopped_at is not None
                else datetime.now(timezone.utc).isoformat()
            )
            _cas_update(
                conn,
                "UPDATE task_runs SET state = 'CLOSED', stopped_at = ? "
                "WHERE id = ? AND state IN ('RUNNING', 'AWAITING_ANSWERS', 'STOPPED')",
                (stopped_at, run.id),
                TaskNotRunningError(f"No open session to close for task {task_id}."),
            )
            return _fetch_run(  # type: ignore[return-value]
                conn, "WHERE id = ?", (run.id,)
            )

    def stop_run(self, task_id: int, timesheet_id: Optional[int] = None) -> TaskRun:
        stopped_at = datetime.now(timezone.utc).isoformat()
        with self._write_txn() as conn:
            run = _fetch_run(conn, _ACTIVE_RUN_WHERE, (task_id,))
            if run is None:
                raise _no_active_session_error(task_id)
            # COALESCE keeps the run's stored timesheet_id when no override is
            # given, without re-reading the row outside the transaction.
            _cas_update(
                conn,
                "UPDATE task_runs SET state = 'STOPPED', stopped_at = ?, "
                "timesheet_id = COALESCE(?, timesheet_id) "
                f"WHERE id = ? AND state IN {_ACTIVE_STATES_SQL}",
                (stopped_at, timesheet_id, run.id),
                _no_active_session_error(task_id),
            )
            return _fetch_run(  # type: ignore[return-value]
                conn, "WHERE id = ?", (run.id,)
            )

    def abort_run(self, task_id: int) -> TaskRun:
        """Force-close the active run to STOPPED and stamp ``aborted_at`` (#356).

        The abort analog of :meth:`stop_run`: it moves the active run straight to
        ``STOPPED`` *and* records the abort instant in the additive ``aborted_at``
        column so the upload path can exclude the run's leftover sessions. The
        stamp is taken as ``now`` — at or after the abort-dispatch agent event
        that lands at abort time — so the aborted window covers that event and it
        can never re-derive a billable session. ``stop_run`` is left
        billing-neutral (a normal stop must still bill), which is why the abort
        stamp lives on a distinct method rather than a flag on ``stop_run``.

        :raises TaskNotRunningError: When there is no active run to abort.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._write_txn() as conn:
            run = _fetch_run(conn, _ACTIVE_RUN_WHERE, (task_id,))
            if run is None:
                raise _no_active_session_error(task_id)
            _cas_update(
                conn,
                "UPDATE task_runs SET state = 'STOPPED', stopped_at = ?, "
                f"aborted_at = ? WHERE id = ? AND state IN {_ACTIVE_STATES_SQL}",
                (now, now, run.id),
                _no_active_session_error(task_id),
            )
            return _fetch_run(  # type: ignore[return-value]
                conn, "WHERE id = ?", (run.id,)
            )

    def latest_event_timestamp_for_task(self, task_id: int) -> Optional[datetime]:
        """Return the most recent event timestamp attributed to ``task_id``, or None.

        The staleness clock for the reaper (#366): a run's "last activity" is the
        latest event carrying its task id — the same ``task_ids`` array the
        derivation and ``--attach-active-run`` write to. Events fan out over their
        task ids with ``json_each`` (a hook event can carry several), so a task
        matches whenever it appears anywhere in the array. Timestamps are stored as
        one uniform UTC isoformat, so a string ``MAX`` is the true chronological
        maximum. The task id is bound as text because ``task_ids`` holds string ids.
        Returns ``None`` when the task has no events on record.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(events.timestamp) "
                "FROM events, json_each(events.task_ids) AS task_each "
                "WHERE task_each.value = ?",
                (str(task_id),),
            ).fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] is not None else None

    def get_aborted_runs(self) -> list[TaskRun]:
        """Return every aborted run (``aborted_at`` stamped), ordered by start.

        The upload path (#356) skips any derived session lying wholly within an
        aborted run's ``[started_at, aborted_at]`` window for the matching task,
        so an aborted run's leftover events never bill. Work done on the same task
        after a fresh ``start_task`` falls in a *later* run window and still bills.
        """
        return self._select_runs("WHERE aborted_at IS NOT NULL ORDER BY started_at")

    def set_run_summary(self, run_id: int, summary: str) -> None:
        """Store the machine-derived run summary on a run row (#626).

        The summary is computed by ``stop_task`` from the run's recorded events
        and notes (:func:`odoo_sdk.state.summary.summarize_run_activity`) and is
        internal/local text: it is deliberately NOT subject to the 300-character
        chatter cap (``enforce_chatter_body_limit``), which applies only to
        chatter bodies posted to Odoo (``task_note`` / ``task_question``).
        """
        with self._connect() as conn:
            conn.execute(
                "UPDATE task_runs SET run_summary = ? WHERE id = ?",
                (summary, run_id),
            )

    def get_runs_for_task(self, task_id: int) -> list[TaskRun]:
        """Return every run recorded for ``task_id`` (CLOSED included), by start.

        A targeted per-task lookup for run-summary consumers (#626): the billing
        upload needs the derived summaries of runs overlapping a session window
        even after a run reached the terminal ``CLOSED`` state, so — unlike
        :meth:`get_all_runs` — closed runs are included here.
        """
        return self._select_runs(
            "WHERE task_id = ? ORDER BY started_at, id", (task_id,)
        )

    def update_timesheet_id(self, run_id: int, timesheet_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE task_runs SET timesheet_id = ? WHERE id = ?",
                (timesheet_id, run_id),
            )

    def append_note(self, task_id: int, note: str) -> None:
        """Append ``note`` to the active run's notes JSON in ONE statement (#627).

        ``json_insert`` with the ``'$[#]'`` append path grows the array in
        place, so the session check (the WHERE clause) and the append are a
        single UPDATE on a single connection: two concurrent appends serialize
        inside SQLite and BOTH land (no read-modify-write lost update), and a
        session stopped mid-call matches no row and raises instead of silently
        resurrecting the note onto a stopped run.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE task_runs SET notes = json_insert(notes, '$[#]', ?) "
                f"WHERE task_id = ? AND state IN {_ACTIVE_STATES_SQL}",
                (note, task_id),
            )
            if cursor.rowcount == 0:
                raise _no_active_session_error(task_id)

    def set_question_watermark(self, task_id: int, message_id: int) -> None:
        """Stamp ``message_id`` as the active run's answer watermark (#625).

        Mirrors :meth:`append_note`: the session check (the WHERE clause) and
        the write are ONE statement, so a session stopped mid-call matches no
        row and raises instead of stamping a watermark onto a stopped run. A
        later question overwrites the previous watermark, so the count exposed
        by ``task_status`` always measures replies to the newest question.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE task_runs SET question_message_id = ? "
                f"WHERE task_id = ? AND state IN {_ACTIVE_STATES_SQL}",
                (message_id, task_id),
            )
            if cursor.rowcount == 0:
                raise _no_active_session_error(task_id)

    def get_chatter_dedupe(self, task_id: int, dedupe_key: str) -> Optional[int]:
        """Return the chatter message id recorded for ``dedupe_key``, or None.

        The read half of the #631 idempotency store: keys are scoped per task
        (the unique index covers ``(task_id, dedupe_key)``), so re-using one key
        on two different tasks never returns the other task's message.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT message_id FROM chatter_dedupe "
                "WHERE task_id = ? AND dedupe_key = ?",
                (task_id, dedupe_key),
            ).fetchone()
        return row[0] if row else None

    def record_chatter_dedupe(
        self, task_id: int, dedupe_key: str, message_id: int
    ) -> bool:
        """Map ``dedupe_key`` to a posted chatter message; True iff newly written.

        ``INSERT OR IGNORE`` against the ``(task_id, dedupe_key)`` unique index
        — the same idempotency primitive as :meth:`add_event_dedup` — so two
        racing posts serialize inside SQLite and the FIRST mapping wins; the
        loser's ``False`` return means the key was already claimed.
        """
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO chatter_dedupe "
                "(task_id, dedupe_key, message_id, created_at) VALUES (?, ?, ?, ?)",
                (task_id, dedupe_key, message_id, created_at),
            )
            return cursor.rowcount == 1

    def remap_timesheet_id(self, old_timesheet_id: int, new_timesheet_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE task_runs SET timesheet_id = ? WHERE timesheet_id = ?",
                (new_timesheet_id, old_timesheet_id),
            )

    # ── Unified event/session model (additive; alongside the FSM store) ──────

    def _insert_event_row(
        self, conn: sqlite3.Connection, event: EventRecord
    ) -> sqlite3.Cursor:
        """Insert one ``events`` row, deduping when ``external_id`` is set.

        Externally-keyed events use ``INSERT OR IGNORE`` so a re-ingested id is a
        no-op against the ``events(external_id)`` partial unique index; events
        with no external id use a plain ``INSERT`` so a genuine constraint
        violation still surfaces rather than being silently swallowed.
        """
        verb = "INSERT OR IGNORE" if event.external_id is not None else "INSERT"
        return conn.execute(
            f"{verb} INTO events (source, timestamp, task_ids, repo, pr_num, "
            "branch, subject, payload, external_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.source,
                _normalize_utc_isoformat(event.timestamp),
                json.dumps(event.task_ids),
                event.repo,
                event.pr_num,
                event.branch,
                event.subject,
                json.dumps(event.payload) if event.payload is not None else None,
                event.external_id,
            ),
        )

    def add_event(self, event: EventRecord) -> EventRecord:
        """Insert one event and return the stored row.

        Idempotent for externally-keyed events: when ``event.external_id`` is
        already present the insert is ignored and the existing row is returned, so
        callers never see a duplicate. Use :meth:`add_event_dedup` when you need
        to know whether a new row was actually written (e.g. to count inserts).
        """
        with self._connect() as conn:
            cursor = self._insert_event_row(conn, event)
            if cursor.rowcount == 1:
                event_id = cursor.lastrowid
            else:
                event_id = conn.execute(
                    "SELECT id FROM events WHERE external_id = ?",
                    (event.external_id,),
                ).fetchone()[0]
        return self.get_event(event_id)  # type: ignore[return-value]

    def add_event_dedup(self, event: EventRecord) -> bool:
        """Insert an externally-keyed event; return True iff a new row was written.

        The idempotency primitive the resync pullers count on: a first ingest of
        an ``external_id`` returns ``True`` (row inserted); any re-ingest of the
        same id returns ``False`` (``INSERT OR IGNORE`` matched the partial unique
        index and did nothing), so a puller can report exactly how many events it
        added.
        """
        with self._connect() as conn:
            return self._insert_event_row(conn, event).rowcount == 1

    def get_event(self, event_id: int) -> Optional[EventRecord]:
        """Return one event by id, or None."""
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {_EVENT_COLUMNS} FROM events WHERE id = ?",
                (event_id,),
            ).fetchone()
        return _parse_event(row) if row else None

    def last_note_at(self, task_id: int) -> Optional[datetime]:
        """Return the timestamp of the most recent recorded ``task_note`` for a task.

        Reads the append-only ``events`` timeseries for the newest
        ``source='agent'`` event whose subject is ``task_note`` and whose
        ``task_ids`` include this task, fanning out the JSON array with
        ``json_each`` so a multi-task event still matches. Returns ``None`` when
        the task has no recorded note event yet. This is the read primitive the
        checkpoint-cadence hint (#387) derives elapsed time from; it never writes
        and is safe to call on every note/start.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(events.timestamp) FROM events, "
                "json_each(events.task_ids) AS task_each "
                "WHERE events.source = 'agent' AND events.subject = 'task_note' "
                "AND task_each.value = ?",
                (str(task_id),),
            ).fetchone()
        ts = row[0] if row is not None else None
        return datetime.fromisoformat(ts) if ts else None

    def get_events(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> list[EventRecord]:
        """Return events ordered by timestamp, optionally bounded by range."""
        where, params = _window_where(start, end)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_EVENT_COLUMNS} FROM events{where} ORDER BY timestamp",
                tuple(params),
            ).fetchall()
        return [_parse_event(r) for r in rows]

    def get_task_events(
        self,
        task_id: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> list[EventRecord]:
        """Return events attributed to ``task_id`` in ``[start, end]``, in order.

        The per-task audit read (#626): fans each event out over its ``task_ids``
        JSON array with ``json_each`` (so a multi-task event still matches) and
        bounds the window INCLUSIVELY on both edges — a run/session's boundary
        events belong to its narrative, so the half-open ``[start, end)``
        convention of :meth:`get_events` is deliberately not used here. Bounds
        are normalized to the uniform stored UTC isoformat, so the string
        comparison is exact. ``DISTINCT`` collapses a task id duplicated within
        one event's array. Ordered by timestamp then id.
        """
        clauses = ["task_each.value = ?"]
        params: list[str] = [str(task_id)]
        if start is not None:
            clauses.append("events.timestamp >= ?")
            params.append(_normalize_utc_isoformat(start))
        if end is not None:
            clauses.append("events.timestamp <= ?")
            params.append(_normalize_utc_isoformat(end))
        columns = ", ".join(f"events.{c}" for c in _EVENT_COLUMNS.split(", "))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT {columns} "
                "FROM events, json_each(events.task_ids) AS task_each "
                f"WHERE {' AND '.join(clauses)} "
                "ORDER BY events.timestamp, events.id",
                tuple(params),
            ).fetchall()
        return [_parse_event(r) for r in rows]

    def get_unattributed_events(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> list[EventRecord]:
        """Return events carrying NO task ids (``task_ids=[]``) in ``[start, end)``.

        The read half of the TUI triage surface (#370, acceptance item 9). An
        event ingested with an empty ``task_ids`` array is invisible to billing —
        the derivation requires ``json_array_length(task_ids) > 0`` — so such an
        event silently never bills unless it is surfaced for triage. This returns
        every unattributed event in the window regardless of ``source``: triage
        must see all ingested-but-unattributed events (calendar meetings, emails,
        diagnostics), not only the sources that would sessionize, so the session
        source predicate is deliberately NOT applied. Ordered by timestamp; the
        window bounds are half-open (``>= start``, ``< end``) to match
        :meth:`get_events` and :meth:`count_events`.
        """
        where, params = _window_where(start, end, ("json_array_length(task_ids) = 0",))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_EVENT_COLUMNS} FROM events{where} ORDER BY timestamp",
                tuple(params),
            ).fetchall()
        return [_parse_event(r) for r in rows]

    def assign_event_task_ids(self, event_ids: list[int], task_id: int) -> int:
        """Attribute every listed event to ``task_id`` in ONE transaction (#370).

        The write half of the triage surface: it sets ``task_ids`` to
        ``[str(task_id)]`` on all ``event_ids`` so a whole calendar series (each
        tick a separate event sharing a ``gcal:<id>:tick:`` external-id prefix) is
        attributed atomically by a single call. Once written the events satisfy
        ``json_array_length(task_ids) > 0`` and immediately become derivable, so
        the meeting bills instead of being silently dropped.

        All chunks execute inside one ``self._connect()`` transaction, so a series
        assignment is all-or-nothing — a failure part-way never leaves half a
        series attributed. Chunking only bounds the per-statement variable count.

        :raises ValueError: When ``task_id`` is not a positive integer. No Odoo
            round-trip validates the id exists — triage only guarantees a
            well-formed, positive task id, not a live one.
        """
        if isinstance(task_id, bool) or not isinstance(task_id, int) or task_id <= 0:
            raise ValueError(f"task_id must be a positive integer, got {task_id!r}")
        if not event_ids:
            return 0
        payload = json.dumps([str(task_id)])
        updated = 0
        with self._connect() as conn:
            for chunk in _chunks(event_ids):
                placeholders = ",".join("?" for _ in chunk)
                cursor = conn.execute(
                    f"UPDATE events SET task_ids = ? WHERE id IN ({placeholders})",
                    (payload, *chunk),
                )
                updated += cursor.rowcount
        return updated

    def derive_sessions_overlapping(
        self,
        start: datetime,
        end: datetime,
        *,
        gap_secs: int,
        task_id: Optional[str] = None,
        repo: Optional[str] = None,
    ) -> list[SessionWindow]:
        """Derive whole sessions overlapping ``[start, end]`` directly from events.

        Gap-based sessionization is computed in one CTE over ``events`` at query
        time (the gap is bound at execution, so nothing materializes or goes stale).
        A session is a maximal run of a single *task*'s events at most ``gap_secs``
        apart, returned whole (its true global bounds), never clipped. The mechanics
        — task-only partitioning (#352), the window prefilter (#359), multi-task
        fan-out (#362), and the development/review labeling (#378 item 6) — are
        documented in full on :data:`_DERIVE_SESSIONS_SQL`.

        **Intentional behavior delta:** events carrying *no* task ids (e.g. most
        MCP-wrapper dispatch events) are stored as diagnostics but NEVER form a
        session — they are filtered out (``json_array_length(task_ids) > 0``).

        ``start``/``end`` bound the overlap window (inclusive). ``task_id`` restricts
        to one task id (any id an event carries, since a multi-task event contributes
        to each). ``repo`` restricts to one *display* repo — a group's real
        ``owner/repo`` when any event carried one, else :data:`AGENTLESS_REPO`
        (pass ``""`` to select purely repo-less sessions). Results are ordered
        by start time.
        """
        margin = _derivation_margin(gap_secs)
        params: dict[str, object] = {
            "agentless": AGENTLESS_REPO,
            "gap_secs": gap_secs,
            "start": _normalize_utc_isoformat(start),
            "end": _normalize_utc_isoformat(end),
            "wstart": _normalize_utc_isoformat(_widen(start, -margin)),
            "wend": _normalize_utc_isoformat(_widen(end, margin)),
        }
        extra = ""
        if task_id is not None:
            extra += " AND task_key = :task_id"
            params["task_id"] = task_id
        if repo is not None:
            # Repo is post-aggregation display metadata (#352), so the filter
            # matches the same COALESCE(MAX(...)) display expression the SELECT
            # projects, applied in the HAVING alongside the overlap predicate.
            extra += " AND COALESCE(MAX(NULLIF(repo, '')), :agentless) = :repo"
            params["repo"] = repo
        sql = _DERIVE_SESSIONS_SQL.format(extra=extra)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_parse_derived_window(r) for r in rows]

    def get_events_by_ids(self, ids: list[int]) -> list[EventRecord]:
        """Return the events with the given ids, in the order requested.

        A bulk fetch used by the derived read path to embed a session's events.
        Ids with no matching row are silently skipped; the returned order mirrors
        ``ids`` (not the table order) so a session's events stay in derivation
        order.
        """
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_EVENT_COLUMNS} FROM events WHERE id IN ({placeholders})",
                tuple(ids),
            ).fetchall()
        by_id = {row[0]: _parse_event(row) for row in rows}
        return [by_id[i] for i in ids if i in by_id]

    def count_events(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> int:
        """Return the number of events, optionally bounded by ``[start, end)``."""
        where, params = _window_where(start, end)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM events{where}", tuple(params)
            ).fetchone()
        return int(row[0])

    def event_ids_before(self, cutoff: datetime) -> list[int]:
        """Return the ids of every event strictly older than ``cutoff``, ascending.

        The retention read primitive (#363): the ``prune`` planner needs the full
        set of aged event ids — including untargeted diagnostic events that never
        form a session — so it can subtract the ids it must protect and delete the
        remainder. ``cutoff`` is normalized to the same uniform UTC isoformat the
        stored timestamps use, so the ``timestamp < ?`` string comparison is exact.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM events WHERE timestamp < ? ORDER BY id",
                (_normalize_utc_isoformat(cutoff),),
            ).fetchall()
        return [int(row[0]) for row in rows]

    def delete_events(self, ids: list[int]) -> int:
        """Delete the events with the given ids and return the number removed.

        The retention write primitive (#363): the sole event-DELETION path in the
        SDK. Ids are deleted in bounded chunks so a heavy day's worth of ids never
        exceeds SQLite's per-statement variable limit. This is a raw delete with no
        guard of its own — the ``prune`` planner is responsible for only ever
        handing it ids it has proven safe to remove (see
        :func:`odoo_sdk.prune.plan_prune`).
        """
        if not ids:
            return 0
        deleted = 0
        with self._connect() as conn:
            for chunk in _chunks(ids):
                placeholders = ",".join("?" for _ in chunk)
                cursor = conn.execute(
                    f"DELETE FROM events WHERE id IN ({placeholders})", tuple(chunk)
                )
                deleted += cursor.rowcount
        return deleted

    def vacuum(self) -> None:
        """Reclaim free pages left by a prune via a full ``VACUUM``.

        A trivial, best-effort space reclaim (#363): after a real prune deletes
        aged rows their pages sit on the freelist until reused, so a one-shot
        ``VACUUM`` rewrites the (small, ephemeral) local DB to hand the space back
        to the filesystem. Run on its own connection so no open transaction can
        make SQLite reject the statement. Reclaim is non-essential (the prune has
        already committed), so a lock held by a concurrent writer — VACUUM needs an
        exclusive lock — is swallowed rather than turned into a spurious failure;
        the busy timeout gives that writer a chance to drain first.

        This never creates the DB (#369): a ``VACUUM`` on a missing file would
        materialize an empty one, so an absent DB is a best-effort no-op (and
        ``mode=rw`` refuses to create one anyway).
        """
        if not self._db_path.exists():
            return
        conn = sqlite3.connect(
            f"file:{self._db_path}?mode=rw", uri=True, isolation_level=None
        )
        conn.execute("PRAGMA busy_timeout=2000")
        try:
            conn.execute("VACUUM")
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

    def get_session_upload(self, session_key: str) -> Optional[dict]:
        """Return the recorded upload for a derived session key, or None.

        The mapping is the idempotency record for per-session timesheet uploads:
        it ties a session's stable key to the single ``account.analytic.line`` id
        it was reconciled onto.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT session_key, timesheet_id, hours, uploaded_at, "
                "task_id, started_at, ended_at "
                "FROM session_uploads WHERE session_key = ?",
                (session_key,),
            ).fetchone()
        return _parse_session_upload(row) if row is not None else None

    def list_session_uploads(self) -> list[dict]:
        """Return every recorded upload mapping (for the orphan sweep, #353).

        The upload sweep diffs these against the set of currently-derived session
        keys for a window: a mapping whose recorded window overlaps the queried
        window but no longer derives has been merged away, so its Odoo row must be
        zeroed and the mapping retired. Local DBs are small and ephemeral, so the
        whole ledger is returned and filtered in Python rather than in SQL.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT session_key, timesheet_id, hours, uploaded_at, "
                "task_id, started_at, ended_at FROM session_uploads"
            ).fetchall()
        return [_parse_session_upload(row) for row in rows]

    def delete_session_upload(self, session_key: str) -> None:
        """Retire a mapping from the ledger once its Odoo row has been zeroed.

        The SDK never deletes Odoo records (see ``forbid_unlink``), but the local
        idempotency ledger is not an Odoo record — an orphaned mapping whose row
        the sweep has zeroed is removed so it is never re-swept.
        """
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM session_uploads WHERE session_key = ?", (session_key,)
            )

    def record_session_upload(
        self,
        session_key: str,
        timesheet_id: int,
        hours: float,
        *,
        task_id: Optional[str] = None,
        started_at: Optional[datetime] = None,
        ended_at: Optional[datetime] = None,
    ) -> None:
        """Upsert the upload mapping for a derived session key.

        Idempotent: re-recording the same key overwrites the mapped timesheet id,
        hours, timestamp, and the ``task_id``/window bounds the orphan sweep keys
        on. The bounds are normalized to the uniform UTC isoformat stored
        timestamps use, so the sweep can string-compare them against a window.
        """
        uploaded_at = datetime.now(timezone.utc).isoformat()
        started = _normalize_utc_isoformat(started_at) if started_at is not None else None
        ended = _normalize_utc_isoformat(ended_at) if ended_at is not None else None
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO session_uploads (session_key, timesheet_id, hours, "
                "uploaded_at, task_id, started_at, ended_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(session_key) DO UPDATE SET "
                "timesheet_id = excluded.timesheet_id, hours = excluded.hours, "
                "uploaded_at = excluded.uploaded_at, task_id = excluded.task_id, "
                "started_at = excluded.started_at, ended_at = excluded.ended_at",
                (session_key, timesheet_id, hours, uploaded_at, task_id, started, ended),
            )

    def get_setting(self, key: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
