"""The events time series, upload ledger, chatter dedupe and settings of ``state.db``.

:class:`EventStore` owns four tables: ``events`` (the append-only time series),
``session_uploads`` (the timesheet upload ledger), ``chatter_dedupe`` (the
idempotency map for posted chatter) and ``settings`` (key/value). Sessions are
derived at query time from ``events`` and never stored. Job-derived windows come
from the logic layer, not from this store.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .db.engine import connect, transaction

# -- vocabulary ---------------------------------------------------------------

# Repo-less agent events cannot key on a real repository, so their derived
# sessions carry an ABSENT repo, the empty string, rather than an in-band
# sentinel. The empty string is never a real ``owner/repo``.
AGENTLESS_REPO = ""

#: Alias of :data:`AGENTLESS_REPO`: both names are the same absent-repo value.
AGENTLESS_REPO_SENTINEL = AGENTLESS_REPO

#: Printable stand-in shown wherever an absent repo needs a human label.
AGENTLESS_REPO_LABEL = "(agent)"


def format_repo_label(repo: Optional[str]) -> str:
    """Return the display label for a session's ``repo``.

    The one place absent-repo display is decided: every consumer routes through
    this helper so they agree.
    """
    return repo if repo else AGENTLESS_REPO_LABEL


@dataclass
class EventRecord:
    """A point-in-time event row in the ``events`` time series.

    Typed by ``source`` (``commit``, ``merge``, ``review``, ``agent``, ...). It
    carries the extracted task identifiers as a list so one event can attribute
    to several tasks.
    """

    id: Optional[int]
    source: str
    timestamp: datetime
    task_ids: list[str]
    repo: str
    pr_num: int = 0
    branch: str = ""
    subject: str = ""
    payload: Optional[dict] = None
    external_id: Optional[str] = None
    """Stable external identity for idempotent resync ingestion.

    Set by the external-sync pullers (``git:<sha>``, ``gh:pr:<n>``,
    ``gh:review:<id>``, ``odoo:mail:<id>``) so a re-run dedupes against the
    partial unique index on ``events(external_id)``. ``None`` for events with no
    external origin, which never dedupe.
    """


@dataclass
class SessionWindow:
    """A per-task computed time window derived from the ``events`` series.

    Windows are not stored: they are computed at query time by
    :meth:`EventStore.derive_sessions_overlapping`.
    """

    id: Optional[int]
    task_id: str
    repo: str
    started_at: datetime
    ended_at: datetime
    strategy_name: str = "development"
    category: str = "Development"
    pr_num: int = 0
    event_ids: tuple[int, ...] = ()
    """Ids of the events that compose this window, in ascending order.

    The window's ``id`` is the minimum of these, so it is stable under
    append-only tail writes (a closed session's earliest event never changes).
    """
    run_session_id: str = ""
    """Session id of the earliest dispatched job in this window.

    Empty for a window made only of events. When set, :func:`session_key` keys
    on it, so the key does not move when events later join or leave the window.
    """
    has_failed: bool = False
    """True when any job in the window ended in the dead-letter state.

    The time is real and recorded, but whether it bills is a human decision at
    upload, so the mark travels to the upload path and the timesheet line.
    """

    @property
    def duration_seconds(self) -> float:
        """Return the window duration in seconds."""
        return (self.ended_at - self.started_at).total_seconds()


def session_key(window: SessionWindow) -> str:
    """Return a stable identity string for a derived session window.

    A job-derived window is keyed ``"{task_id}|run:{session_id}"`` on its
    earliest job, which is write-once and stable. Otherwise the key is
    ``"{task_id}|{id}"`` where ``id`` is the window's minimum event id.
    Sessions partition by task only (the repo is display metadata), so a task's
    agent events (``repo=""``) and its resync'd commits (``repo="owner/repo"``)
    share one key. A closed session's earliest event id is immutable under
    append-only tail writes, so the key is stable across re-derivations and is
    the idempotency key for per-session timesheet uploads.
    """
    if window.run_session_id:
        return f"{window.task_id}|run:{window.run_session_id}"
    return f"{window.task_id}|{window.id}"


# -- helpers ------------------------------------------------------------------


def _as_utc(ts: datetime) -> datetime:
    """Return ``ts`` as an aware UTC datetime; a naive value is taken as UTC."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


# Max ids bound into a single ``... IN (...)`` statement. Chunked to stay well
# under SQLite's historical 999-variable limit; each caller runs all chunks
# inside one transaction so the whole operation stays atomic.
_ID_CHUNK = 500


def _chunks(seq: Sequence[int], size: int = _ID_CHUNK):
    """Yield successive ``size``-length slices of ``seq``."""
    for start in range(0, len(seq), size):
        yield seq[start : start + size]


# Development-family sources: ``commit``, ``agent``, ``chatter`` resync events,
# the ``calendar`` meeting ticks and ``email`` sent-mail point events, plus the
# open-ended ``claude:<HookName>`` family. Calendar ticks are synthetic point
# events emitted 5 min apart across a meeting so the gap derivation rebuilds the
# meeting as one session. A derived group containing ANY development-family
# event is labeled "Development".
_DEVELOPMENT_SOURCE_PREDICATE = (
    "(source IN ('commit', 'agent', 'chatter', 'calendar', 'email') "
    "OR source LIKE 'claude:%')"
)

# Review-family sources: submitted PR ``review`` passes, authored PR/issue
# ``comment`` events and PR-opening ``pr_opened`` events. Review activity is
# bursty (one pass of 33 inline comments, or a stack of PRs opened minutes
# apart); gap-windowing makes the burst one session and a lone review a
# single-event session that floors to the minimum. A group with ONLY
# review-family events is labeled "Review" (development wins a mixed group).
# ``merge`` stays OUT of the derivation: it is a point-in-time release marker
# kept for audit, not a work span.
_REVIEW_SOURCE_PREDICATE = "source IN ('review', 'comment', 'pr_opened')"

# Sources whose events participate in gap-based sessionization: the union of the
# development and review families. ``merge`` is the only ingested source excluded.
_SESSION_SOURCE_PREDICATE = (
    f"({_DEVELOPMENT_SOURCE_PREDICATE} OR {_REVIEW_SOURCE_PREDICATE})"
)


# CTE that reproduces gap-based sessionization directly over ``events`` at query
# time. The inactivity gap is bound at execution (SQLite views cannot take a
# parameter), so nothing is materialized and nothing goes stale. The
# consecutive-event delta is ``ROUND``ed to whole seconds before the gap
# comparison: ``julianday`` arithmetic carries a float epsilon that would
# otherwise make two events *exactly* ``gap_secs`` apart read as > the gap and
# spuriously split. Event timestamps are second-resolution for sessionization,
# so rounding is exact at the boundary.
#
# Partitioning: sessions partition by ``task_key`` ALONE; the repo is not a
# partition key. Agent/hook/MCP events carry ``repo=""`` while resync
# commit/chatter events carry the real ``owner/repo`` label; keying on repo
# would split one task's span into two parallel lanes and bill it twice. Repo
# survives as display metadata: ``COALESCE(MAX(NULLIF(repo,'')), :agentless)``
# per group prefers the real label and falls back to an absent repo. Distinct
# *tasks* still partition separately, so concurrent tasks bill in parallel.
#
# Window prefilter: the ``base`` CTE bounds ``timestamp`` to the queried
# ``[start, end]`` widened by :func:`_derivation_margin` each side, so a refresh
# scans a slice via ``idx_events_timestamp`` instead of sessionizing every event
# ever recorded. The margin (``max(gap_secs, 1 day)``) is wide enough that a
# session merely straddling the queried window still pulls in its neighbours
# whole. A session whose total span exceeds the margin can be clipped at the far
# edge (documented, accepted tradeoff: no fixed margin bounds a gap chain).
#
# Fan-out: a multi-task event (``task_ids=[t1, t2]``) is fanned out over its
# task ids with ``json_each``: it yields one ``base`` row per task id and thus
# extends BOTH tasks' sessions. ``events.id`` is qualified because ``json_each``
# also exposes an ``id`` column. ``DISTINCT`` collapses a task id that appears
# twice in one array so a duplicated id never double-counts an event within its
# own session. One event id can anchor two tasks' sessions; keys stay distinct
# because the session key is ``task|min_event_id`` (task-scoped).
#
# Category: each ``base`` row carries an ``is_dev`` flag (1 for a
# development-family source, 0 for a review-family one); ``MAX(is_dev)`` per
# group is the label decision. A group with ANY development-family event is
# "Development"; a group of purely review-family events is "Review". The flag
# derives from ``source`` alone, so it does not perturb the ``DISTINCT``.
#
# Task attribution: the ``json_array_length(events.task_ids) > 0`` filter below,
# with ``COALESCE(NULLIF(value, ''), 'UNKNOWN')`` bucketing empty ids, is the
# canonical attribution predicate. An event with no task ids is stored as a
# diagnostic but NEVER forms a session.
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

# Columns selected for every event read, in EventRecord field order.
_EVENT_COLUMNS = (
    "id, source, timestamp, task_ids, repo, pr_num, branch, subject, payload, "
    "external_id"
)

_SESSION_UPLOAD_COLUMNS = (
    "session_key, timesheet_id, hours, uploaded_at, task_id, started_at, ended_at"
)


def normalize_utc_isoformat(ts: datetime) -> str:
    """Return ``ts`` as a uniform UTC isoformat string, used for stored values and bounds.

    The derived read path compares ``MIN/MAX(timestamp)`` and query bounds as
    strings, which is only correct when every value shares one UTC offset. An
    aware timestamp is converted to UTC. A naive one (a stored value, a window
    edge, or the ``datetime.min``/``datetime.max`` sentinel) is treated as
    already UTC and stamped with ``+00:00``, so all rows and bounds sort and
    compare uniformly.
    """
    return _as_utc(ts).isoformat()


def _derivation_margin(gap_secs: int) -> timedelta:
    """Return how far the derivation prefilter widens the queried window.

    A gap-based session is a chain of events each at most ``gap_secs`` apart, so
    no fixed margin guarantees a session's whole span is captured. Widening by
    ``max(gap_secs, 1 day)`` each side keeps an event on the boundary chained to
    its neighbour and covers a typical session that straddles the window. A
    session longer than the margin can be clipped at its far edge.
    """
    return timedelta(seconds=max(gap_secs, 86_400))


def _widen(ts: datetime, delta: timedelta) -> datetime:
    """Return ``ts + delta``, clamped to ``datetime.min``/``max`` on over/underflow.

    The query layer passes ``datetime.min``/``datetime.max`` for an unbounded
    edge, and shifting those overflows. Clamping by the sign of ``delta`` keeps
    the widened bound a valid sentinel that still sorts past everything.
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
    stored-timestamp string form. ``extra_clauses`` are ANDed ahead of the bounds.
    """
    clauses = list(extra_clauses)
    params: list[str] = []
    if start is not None:
        clauses.append("timestamp >= ?")
        params.append(normalize_utc_isoformat(start))
    if end is not None:
        clauses.append("timestamp < ?")
        params.append(normalize_utc_isoformat(end))
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def _parse_event(row: sqlite3.Row) -> EventRecord:
    id_, source, ts, task_ids, repo, pr_num, branch, subject, payload, ext_id = tuple(
        row
    )
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


def _parse_session_upload(row: sqlite3.Row) -> dict:
    """Shape a ``session_uploads`` row into the accessor's dict.

    ``task_id``/``started_at``/``ended_at`` are ``None`` for rows written
    without the orphan-discovery columns.
    """
    return dict(zip(_SESSION_UPLOAD_COLUMNS.split(", "), tuple(row)))


def _parse_derived_window(row: sqlite3.Row) -> SessionWindow:
    """Build a :class:`SessionWindow` from a ``derive_sessions_overlapping`` row.

    The row carries ``(task_key, repo_display, session_key_id, started_at,
    ended_at, pr_num, has_dev, event_ids)`` where ``event_ids`` is a JSON array.
    ``repo_display`` is the group's real ``owner/repo`` label when any event
    carried one, else :data:`AGENTLESS_REPO`. Render it through
    :func:`format_repo_label`. ``id`` is the session's minimum event id.

    ``has_dev`` labels the window: ``1`` when the group holds any
    development-family event gives ``development`` / ``Development``. ``0``
    (purely review-family) gives ``review`` / ``Review``.

    ``event_ids`` is sorted ascending in Python: ``json_group_array`` has no
    order guarantee, so sorting by id (monotonic with insertion) gives a
    deterministic order for the bulk event fetch.
    """
    (
        task_key,
        repo_display,
        session_key_id,
        started,
        ended,
        pr_num,
        has_dev,
        event_ids_json,
    ) = tuple(row)
    strategy_name, category = (
        ("development", "Development") if has_dev else ("review", "Review")
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


def _insert_event_row(conn: sqlite3.Connection, event: EventRecord) -> sqlite3.Cursor:
    """Insert one ``events`` row, deduping when ``external_id`` is set.

    Externally-keyed events use ``INSERT OR IGNORE`` so a re-ingested id is a
    no-op against the ``events(external_id)`` partial unique index. Events with
    no external id use a plain ``INSERT`` so a genuine constraint violation
    surfaces rather than being swallowed.
    """
    verb = "INSERT OR IGNORE" if event.external_id is not None else "INSERT"
    return conn.execute(
        f"{verb} INTO events (source, timestamp, task_ids, repo, pr_num, "
        "branch, subject, payload, external_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event.source,
            normalize_utc_isoformat(event.timestamp),
            json.dumps(event.task_ids),
            event.repo,
            event.pr_num,
            event.branch,
            event.subject,
            json.dumps(event.payload) if event.payload is not None else None,
            event.external_id,
        ),
    )


# -- store --------------------------------------------------------------------


class EventStore:
    """Events, upload ledger, chatter dedupe and settings on one held connection.

    Pass ``conn`` to share an existing ``state.db`` connection, or ``home`` to
    open one. Every method runs under one lock, so the store is safe to share
    across threads. Writes run in ``BEGIN IMMEDIATE`` transactions.
    """

    def __init__(
        self, conn: sqlite3.Connection | None = None, *, home: Path | None = None
    ) -> None:
        self._conn = conn if conn is not None else connect(home)
        self._lock = threading.Lock()

    def close(self) -> None:
        """Checkpoint the WAL (best effort) and close the connection."""
        with self._lock:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.OperationalError:
                pass
            self._conn.close()

    def __enter__(self) -> EventStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            yield self._conn

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._lock, transaction(self._conn) as conn:
            yield conn

    # -- events ---------------------------------------------------------------

    @staticmethod
    def _select_event(conn: sqlite3.Connection, event_id: int) -> Optional[EventRecord]:
        row = conn.execute(
            f"SELECT {_EVENT_COLUMNS} FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        return _parse_event(row) if row else None

    def add_event(self, event: EventRecord) -> EventRecord:
        """Insert one event and return the stored row.

        Idempotent for externally-keyed events: when ``event.external_id`` is
        already present the insert is ignored and the existing row is returned.
        Use :meth:`add_event_dedup` to learn whether a new row was written.
        """
        with self._write() as conn:
            cursor = _insert_event_row(conn, event)
            if cursor.rowcount == 1:
                event_id = cursor.lastrowid
            else:
                event_id = conn.execute(
                    "SELECT id FROM events WHERE external_id = ?",
                    (event.external_id,),
                ).fetchone()[0]
            return self._select_event(conn, event_id)  # type: ignore[arg-type,return-value]

    def add_event_dedup(self, event: EventRecord) -> bool:
        """Insert an externally-keyed event; return True iff a new row was written.

        The idempotency primitive the resync pullers count on: a first ingest of
        an ``external_id`` returns ``True``. Any re-ingest of the same id returns
        ``False`` (``INSERT OR IGNORE`` matched the partial unique index).
        """
        with self._write() as conn:
            return _insert_event_row(conn, event).rowcount == 1

    def get_event(self, event_id: int) -> Optional[EventRecord]:
        """Return one event by id, or None."""
        with self._read() as conn:
            return self._select_event(conn, event_id)

    def last_note_at(self, task_id: int) -> Optional[datetime]:
        """Return the timestamp of the most recent ``task_note`` event for a task.

        Reads the newest ``source='agent'`` event whose subject is ``task_note``
        and whose ``task_ids`` include this task (fanned out with ``json_each``,
        so a multi-task event still matches). Returns ``None`` when the task has
        no note event. It never writes.
        """
        with self._read() as conn:
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
        """Return events ordered by timestamp, optionally bounded by ``[start, end)``."""
        where, params = _window_where(start, end)
        with self._read() as conn:
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

        The per-task audit read: fans each event out over its ``task_ids`` array
        with ``json_each`` (so a multi-task event still matches) and bounds the
        window INCLUSIVELY on both edges, because a session's boundary events
        belong to its narrative. This differs on purpose from the half-open
        ``[start, end)`` of :meth:`get_events`. ``DISTINCT`` collapses a task id
        duplicated within one event. Ordered by timestamp, then id.
        """
        clauses = ["task_each.value = ?"]
        params: list[str] = [str(task_id)]
        if start is not None:
            clauses.append("events.timestamp >= ?")
            params.append(normalize_utc_isoformat(start))
        if end is not None:
            clauses.append("events.timestamp <= ?")
            params.append(normalize_utc_isoformat(end))
        columns = ", ".join(f"events.{c}" for c in _EVENT_COLUMNS.split(", "))
        with self._read() as conn:
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

        The read half of triage. An event with empty ``task_ids`` never bills,
        because the derivation requires ``json_array_length(task_ids) > 0``. This
        returns every unattributed event in the window regardless of ``source``,
        so the session source predicate is deliberately NOT applied. Ordered by
        timestamp. The bounds are half-open, like :meth:`get_events`.
        """
        where, params = _window_where(start, end, ("json_array_length(task_ids) = 0",))
        with self._read() as conn:
            rows = conn.execute(
                f"SELECT {_EVENT_COLUMNS} FROM events{where} ORDER BY timestamp",
                tuple(params),
            ).fetchall()
        return [_parse_event(r) for r in rows]

    def assign_event_task_ids(self, event_ids: list[int], task_id: int) -> int:
        """Attribute every listed event to ``task_id`` in ONE transaction.

        The write half of triage: it sets ``task_ids`` to ``[str(task_id)]`` on
        all ``event_ids``, so a whole calendar series is attributed atomically.
        The events then satisfy ``json_array_length(task_ids) > 0`` and become
        derivable. All chunks run in one transaction, so a failure part-way never
        leaves half a series attributed. Return the number of rows updated.

        :raises ValueError: When ``task_id`` is not a positive integer.
        """
        if isinstance(task_id, bool) or not isinstance(task_id, int) or task_id <= 0:
            raise ValueError(f"task_id must be a positive integer, got {task_id!r}")
        if not event_ids:
            return 0
        payload = json.dumps([str(task_id)])
        updated = 0
        with self._write() as conn:
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
        """Derive whole sessions overlapping ``[start, end]`` from events alone.

        Gap-based sessionization is computed in one CTE over ``events`` at query
        time. A session is a maximal run of one task's events at most ``gap_secs``
        apart, returned whole (its true global bounds), never clipped. The
        mechanics are documented on :data:`_DERIVE_SESSIONS_SQL`.

        Events carrying no task ids are stored as diagnostics but NEVER form a
        session. ``start``/``end`` bound the overlap window (inclusive).
        ``task_id`` restricts to one task id (any id an event carries). ``repo``
        restricts to one display repo: a group's real ``owner/repo`` when any
        event carried one, else :data:`AGENTLESS_REPO` (pass ``""`` for purely
        repo-less sessions). Results are ordered by start time.
        """
        margin = _derivation_margin(gap_secs)
        params: dict[str, object] = {
            "agentless": AGENTLESS_REPO,
            "gap_secs": gap_secs,
            "start": normalize_utc_isoformat(start),
            "end": normalize_utc_isoformat(end),
            "wstart": normalize_utc_isoformat(_widen(start, -margin)),
            "wend": normalize_utc_isoformat(_widen(end, margin)),
        }
        extra = ""
        if task_id is not None:
            extra += " AND task_key = :task_id"
            params["task_id"] = task_id
        if repo is not None:
            # Repo is post-aggregation display metadata, so the filter matches
            # the same COALESCE(MAX(...)) expression the SELECT projects.
            extra += " AND COALESCE(MAX(NULLIF(repo, '')), :agentless) = :repo"
            params["repo"] = repo
        sql = _DERIVE_SESSIONS_SQL.format(extra=extra)
        with self._read() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_parse_derived_window(r) for r in rows]

    def get_events_by_ids(self, ids: list[int]) -> list[EventRecord]:
        """Return the events with the given ids, in the order requested.

        Ids with no matching row are silently skipped. The returned order mirrors
        ``ids`` (not the table order), so a session's events stay in derivation
        order.
        """
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self._read() as conn:
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
        with self._read() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM events{where}", tuple(params)
            ).fetchone()
        return int(row[0])

    # -- upload ledger --------------------------------------------------------

    def get_session_upload(self, session_key: str) -> Optional[dict]:
        """Return the recorded upload for a derived session key, or None.

        The mapping is the idempotency record for per-session timesheet uploads:
        it ties a session's stable key to the one ``account.analytic.line`` id it
        was reconciled onto.
        """
        with self._read() as conn:
            row = conn.execute(
                f"SELECT {_SESSION_UPLOAD_COLUMNS} "
                "FROM session_uploads WHERE session_key = ?",
                (session_key,),
            ).fetchone()
        return _parse_session_upload(row) if row is not None else None

    def list_session_uploads(self) -> list[dict]:
        """Return every recorded upload mapping, for the orphan sweep.

        The sweep diffs these against the currently derived session keys for a
        window. A mapping whose window overlaps but no longer derives has been
        merged away, so its Odoo row is zeroed and the mapping retired. The
        ledger is small, so it is filtered in Python rather than in SQL.
        """
        with self._read() as conn:
            rows = conn.execute(
                f"SELECT {_SESSION_UPLOAD_COLUMNS} FROM session_uploads"
            ).fetchall()
        return [_parse_session_upload(row) for row in rows]

    def delete_session_upload(self, session_key: str) -> None:
        """Retire a mapping from the ledger once its Odoo row has been zeroed."""
        with self._write() as conn:
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

        Idempotent: re-recording the same key overwrites the timesheet id, hours,
        timestamp, and the ``task_id``/window bounds the orphan sweep keys on. The
        bounds are normalized to the uniform UTC isoformat of stored timestamps,
        so the sweep can string-compare them against a window.
        """
        uploaded_at = datetime.now(timezone.utc).isoformat()
        started = (
            normalize_utc_isoformat(started_at) if started_at is not None else None
        )
        ended = normalize_utc_isoformat(ended_at) if ended_at is not None else None
        with self._write() as conn:
            conn.execute(
                "INSERT INTO session_uploads (session_key, timesheet_id, hours, "
                "uploaded_at, task_id, started_at, ended_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(session_key) DO UPDATE SET "
                "timesheet_id = excluded.timesheet_id, hours = excluded.hours, "
                "uploaded_at = excluded.uploaded_at, task_id = excluded.task_id, "
                "started_at = excluded.started_at, ended_at = excluded.ended_at",
                (
                    session_key,
                    timesheet_id,
                    hours,
                    uploaded_at,
                    task_id,
                    started,
                    ended,
                ),
            )

    def remap_timesheet_id(self, old_timesheet_id: int, new_timesheet_id: int) -> None:
        """Point every upload mapping at ``old_timesheet_id`` to ``new_timesheet_id``."""
        with self._write() as conn:
            conn.execute(
                "UPDATE session_uploads SET timesheet_id = ? WHERE timesheet_id = ?",
                (new_timesheet_id, old_timesheet_id),
            )

    # -- chatter dedupe -------------------------------------------------------

    def get_chatter_dedupe(self, task_id: int, dedupe_key: str) -> Optional[int]:
        """Return the chatter message id recorded for ``dedupe_key``, or None.

        Keys are scoped per task (the unique index covers ``(task_id,
        dedupe_key)``), so one key on two tasks never returns the other task's
        message.
        """
        with self._read() as conn:
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
        (the primitive of :meth:`add_event_dedup`), so two racing posts serialize
        inside SQLite and the FIRST mapping wins. A ``False`` return means the
        key was already claimed.
        """
        created_at = datetime.now(timezone.utc).isoformat()
        with self._write() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO chatter_dedupe "
                "(task_id, dedupe_key, message_id, created_at) VALUES (?, ?, ?, ?)",
                (task_id, dedupe_key, message_id, created_at),
            )
            return cursor.rowcount == 1

    # -- settings -------------------------------------------------------------

    def get_setting(self, key: str) -> Optional[str]:
        """Return the value stored for ``key``, or None."""
        with self._read() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        """Store ``value`` under ``key``, replacing any earlier value."""
        with self._write() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
