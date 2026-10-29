import re
import sqlite3

import pytest

from odoo_dev_data.db import (
    MIGRATIONS,
    Migration,
    MigrationError,
    apply_migrations,
    connect,
    open_store,
    schema_version,
)
from odoo_dev_data.db import engine
from odoo_dev_data.home import ENV_HOME

from .legacy_schema import LEGACY_TRACKER_DDL

T0 = 1_700_000_000_000_000
SHARED_TABLES = {"events", "session_uploads", "chatter_dedupe", "settings"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_HOME, str(tmp_path))
    return tmp_path


def _ledger(conn):
    rows = conn.execute("SELECT version, name, applied_us FROM schema_migrations")
    return [tuple(r) for r in rows]


# -- connect ------------------------------------------------------------------


def test_connect_creates_state_db_and_applies_both_migrations(home):
    conn = connect()
    assert (home / "state.db").is_file()
    ledger = _ledger(conn)
    assert [(v, n) for v, n, _ in ledger] == [
        (1, "events"),
        (2, "jobs"),
        (3, "jobs_handled"),
    ]
    assert all(applied > 0 for _, _, applied in ledger)
    assert schema_version(conn) == 3


def test_second_connect_applies_nothing(home):
    first = connect(now_us=T0)
    before = _ledger(first)
    first.close()
    second = connect(now_us=T0 + 99)
    assert _ledger(second) == before


def test_connect_pragmas(home):
    conn = connect()
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA recursive_triggers").fetchone()[0] == 1
    assert conn.isolation_level is None


def test_now_us_is_integer_microseconds():
    assert isinstance(engine.now_us(), int)
    assert engine.now_us() > 1_600_000_000_000_000


def test_schema_version_without_a_ledger_is_zero():
    assert schema_version(sqlite3.connect(":memory:")) == 0


# -- parity with the legacy tracker schema ------------------------------------


def _normalize(sql):
    sql = re.sub(r"IF NOT EXISTS\s+", "", sql)
    return re.sub(r"\s+", " ", sql).strip()


def _objects(conn):
    rows = conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master")
    return {(t, n, tbl, _normalize(sql)) for t, n, tbl, sql in rows if sql}


def test_shared_tables_match_the_legacy_schema():
    legacy = sqlite3.connect(":memory:")
    legacy.executescript(LEGACY_TRACKER_DDL)
    new = sqlite3.connect(":memory:", isolation_level=None)
    apply_migrations(new, MIGRATIONS, T0)

    legacy_all, new_all = _objects(legacy), _objects(new)
    assert "task_runs" in {tbl for _, _, tbl, _ in legacy_all}
    assert "task_runs" not in {tbl for _, _, tbl, _ in new_all}

    def shared(objs):
        return {o for o in objs if o[2] in SHARED_TABLES}

    assert shared(new_all) == shared(legacy_all)
    assert len(shared(new_all)) == 7  # four tables and three indexes


# -- the jobs table -----------------------------------------------------------

INSERT = (
    "INSERT INTO jobs (job_id, batch_id, task_id, repo, state, brief, "
    "submitted_us{extra}) VALUES (?, 'b', ?, 'r', ?, 'do it', ?{marks})"
)


def _insert(conn, job_id, task_id="1", state="PENDING", **extra):
    cols = "".join(f", {c}" for c in extra)
    marks = "".join(", ?" for _ in extra)
    conn.execute(
        INSERT.format(extra=cols, marks=marks),
        (job_id, task_id, state, T0, *extra.values()),
    )


@pytest.fixture
def conn(home):
    return connect(now_us=T0)


def test_pending_job_reads_back_the_defaults(conn):
    _insert(conn, "j1")
    row = conn.execute("SELECT * FROM jobs WHERE job_id = 'j1'").fetchone()
    assert row["entry"] == ""
    assert row["question_count"] == 0
    assert row["cost_usd"] == 0
    assert row["approved_us"] is None
    assert row["harvested_us"] is None
    assert row["envelope"] == ""
    assert row["submitted_at"].startswith("2023-11-14T")


def test_one_live_job_per_task(conn):
    _insert(conn, "j1")
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, "j2")
    _insert(conn, "j3", task_id="2")


def test_running_job_needs_started_us(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, "j1", state="RUNNING")
    _insert(conn, "j2", state="RUNNING", started_us=T0 + 1)


def test_envelope_must_be_json_or_empty(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, "j1", envelope="{bad")
    _insert(conn, "j2", envelope='{"a":1}')


# -- the runner ---------------------------------------------------------------


def _tables(*names):
    return tuple(
        Migration(i, n, (f"CREATE TABLE {n} (x INTEGER) STRICT",))
        for i, n in enumerate(names, start=1)
    )


def test_migrations_apply_once_and_are_recorded(tmp_path):
    migs = _tables("a", "b")
    conn = open_store(tmp_path / "m.db", migs, synchronous="NORMAL", now_us=T0)
    assert apply_migrations(conn, migs, T0) == []
    assert _ledger(conn) == [(1, "a", T0), (2, "b", T0)]
    assert apply_migrations(conn, _tables("a", "b", "c"), T0 + 1) == [3]


def test_failed_migration_rolls_back_and_is_not_recorded(tmp_path):
    migs = (Migration(1, "bad", ("CREATE TABLE a (x INTEGER) STRICT", "BOGUS SQL")),)
    with pytest.raises(sqlite3.OperationalError):
        open_store(tmp_path / "m.db", migs, synchronous="NORMAL", now_us=T0)
    conn = sqlite3.connect(tmp_path / "m.db")
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master")]
    assert "a" not in names


def test_migration_recorded_by_a_racing_process_is_skipped(tmp_path):
    conn = open_store(tmp_path / "m.db", (), synchronous="NORMAL", now_us=T0)
    conn.execute("INSERT INTO schema_migrations VALUES (1, 'a', 1)")
    engine._apply_one(conn, _tables("a")[0], T0)
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master")]
    assert "a" not in names


def test_migration_numbering_must_be_contiguous(tmp_path):
    conn = sqlite3.connect(tmp_path / "m.db", isolation_level=None)
    with pytest.raises(MigrationError):
        apply_migrations(conn, (Migration(2, "x", ()),), T0)


def test_open_store_rejects_bad_synchronous(tmp_path):
    with pytest.raises(ValueError):
        open_store(tmp_path / "m.db", (), synchronous="OFF", now_us=T0)
