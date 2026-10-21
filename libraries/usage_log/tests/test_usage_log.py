import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import usage_log
from usage_log import cli

SRC = str(Path(__file__).resolve().parents[1] / "src")


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    target = tmp_path / "log"
    monkeypatch.setenv("USAGE_LOG_DIR", str(target))
    monkeypatch.delenv("ODOO_JOB_ID", raising=False)
    return target


def rows(directory, db, sql):
    conn = sqlite3.connect(directory / db)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_record_writes_envelope_and_context(log_dir):
    assert usage_log.record("Skill", " /odoo-dev:plan ", source="odoo-dev", n=2)
    ((ts, kind, name, context),) = rows(log_dir, "usage.db", "SELECT * FROM usage")
    assert (kind, name) == ("skill", "odoo-dev:plan")
    assert json.loads(context) == {"source": "odoo-dev", "n": 2}
    assert ts.endswith("+00:00")


def test_wal_and_index(log_dir):
    usage_log.record("hook", "x")
    assert rows(log_dir, "usage.db", "PRAGMA journal_mode") == [("wal",)]
    assert rows(log_dir, "usage.db", "PRAGMA index_list(usage)")


def test_job_id_from_env_but_explicit_wins(log_dir, monkeypatch):
    monkeypatch.setenv("ODOO_JOB_ID", "j1")
    usage_log.record("hook", "a")
    usage_log.record("hook", "b", job_id="other")
    got = [
        json.loads(c)["job_id"]
        for (c,) in rows(log_dir, "usage.db", "SELECT context FROM usage ORDER BY name")
    ]
    assert got == ["j1", "other"]


def test_context_may_name_kind(log_dir):
    assert usage_log.record("hook", "a", kind="x", name="y")


@pytest.mark.parametrize("value", [None, "", "  "])
def test_unset_or_empty_does_nothing(value, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    if value is None:
        monkeypatch.delenv("USAGE_LOG_DIR", raising=False)
    else:
        monkeypatch.setenv("USAGE_LOG_DIR", value)
    assert usage_log.record("hook", "a") is False
    assert usage_log.declare([("hook", "a")]) is False
    assert cli.main(["record", "hook", "a"]) == 0
    assert list(tmp_path.iterdir()) == []


def test_declare_upserts_and_normalises(log_dir):
    usage_log.declare(
        [{"kind": "Skill", "name": "/a:b", "context": {"v": 1}}, ("agent", "c")]
    )
    usage_log.declare([("skill", "a:b", {"v": 2})])
    got = rows(
        log_dir,
        "declarations.db",
        "SELECT kind, name, context FROM declarations ORDER BY kind",
    )
    assert got == [("agent", "c", "{}"), ("skill", "a:b", '{"v": 2}')]


def test_declare_bad_item_is_dropped(log_dir, capsys):
    assert usage_log.declare([{"kind": "x"}]) is False
    assert "usage-log: dropped" in capsys.readouterr().err


def test_unwritable_dir_drops_with_one_stderr_line(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("USAGE_LOG_DIR", str(blocker / "sub"))
    assert usage_log.record("hook", "a") is False
    err = capsys.readouterr().err
    assert err.startswith("usage-log: dropped") and err.count("\n") == 1


def run_cli(env_dir, *args, stdin=None):
    env = {**os.environ, "PYTHONPATH": SRC, "USAGE_LOG_DIR": env_dir}
    return subprocess.run(
        [sys.executable, "-m", "usage_log.cli", *args],
        input=stdin,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def test_cli_record_and_declare(log_dir):
    assert run_cli(str(log_dir), "record", "hook", "h", "a=1", "junk").returncode == 0
    done = run_cli(str(log_dir), "declare", stdin='[{"kind": "hook", "name": "h"}]')
    assert done.returncode == 0
    context = rows(log_dir, "usage.db", "SELECT context FROM usage")[0][0]
    assert json.loads(context) == {"a": "1"}
    assert rows(log_dir, "declarations.db", "SELECT kind, name FROM declarations") == [
        ("hook", "h")
    ]


def test_cli_bad_json_exits_zero(log_dir):
    done = run_cli(str(log_dir), "declare", stdin="not json")
    assert done.returncode == 0 and "dropped" in done.stderr


def test_cli_missing_dir_exits_zero(tmp_path):
    blocker = tmp_path / "f"
    blocker.write_text("x")
    done = run_cli(str(blocker / "d"), "record", "hook", "a")
    assert done.returncode == 0 and done.stderr.count("\n") == 1


def test_cli_usage_error(log_dir):
    assert run_cli(str(log_dir), "bogus").returncode == 2


WRITER = (
    "import usage_log, sys\n"
    "for i in range(200):\n"
    "    usage_log.record('hook', 'p' + sys.argv[1], i=i)\n"
)


def test_concurrent_writers_lose_nothing(log_dir):
    env = {**os.environ, "PYTHONPATH": SRC, "USAGE_LOG_DIR": str(log_dir)}
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", WRITER, str(n)],
            env=env,
            stderr=subprocess.PIPE,
            text=True,
        )
        for n in range(50)
    ]
    outcomes = [(p.wait(), p.stderr.read()) for p in procs]
    assert all(code == 0 for code, _ in outcomes)
    assert all(err == "" for _, err in outcomes)
    assert rows(log_dir, "usage.db", "SELECT count(*) FROM usage") == [(10000,)]
    assert rows(log_dir, "usage.db", "SELECT count(DISTINCT name) FROM usage") == [
        (50,)
    ]
