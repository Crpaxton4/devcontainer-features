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
    target.mkdir()
    monkeypatch.setenv("ODOO_DEV_HOME", str(target))
    monkeypatch.delenv("ODOO_DEV_USAGE_LOG", raising=False)
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


def test_switch_zero_disables(log_dir, monkeypatch):
    monkeypatch.setenv("ODOO_DEV_USAGE_LOG", "0")
    assert usage_log.record("hook", "a") is False
    assert usage_log.declare([("hook", "a")]) is False
    assert cli.main(["record", "hook", "a"]) == 0
    assert list(log_dir.iterdir()) == []


@pytest.mark.parametrize("value", [None, "1", "", "false"])
def test_other_switch_values_enable(log_dir, monkeypatch, value):
    if value is not None:
        monkeypatch.setenv("ODOO_DEV_USAGE_LOG", value)
    assert usage_log.record("hook", "a") is True
    assert (log_dir / "usage.db").exists()


def test_missing_root_writes_nothing_and_creates_nothing(tmp_path, monkeypatch, capsys):
    root = tmp_path / "absent"
    monkeypatch.setenv("ODOO_DEV_HOME", str(root))
    monkeypatch.delenv("ODOO_DEV_USAGE_LOG", raising=False)
    assert usage_log.record("hook", "a") is False
    assert usage_log.declare([("hook", "a")]) is False
    assert cli.main(["record", "hook", "a"]) == 0
    assert not root.exists()
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("value", [None, ""])
def test_default_root_uses_home(tmp_path, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("ODOO_DEV_HOME", raising=False)
    else:
        monkeypatch.setenv("ODOO_DEV_HOME", value)
    monkeypatch.delenv("ODOO_DEV_USAGE_LOG", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".config" / "odoo-dev"
    root.mkdir(parents=True)
    assert usage_log.record("hook", "a") is True
    assert (root / "usage.db").exists()


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


def test_unwritable_db_drops_with_one_stderr_line(log_dir, capsys):
    (log_dir / "usage.db").mkdir()
    assert usage_log.record("hook", "a") is False
    err = capsys.readouterr().err
    assert err.startswith("usage-log: dropped") and err.count("\n") == 1


def run_cli(env_dir, *args, stdin=None):
    env = {**os.environ, "PYTHONPATH": SRC, "ODOO_DEV_HOME": env_dir}
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


def test_cli_failed_write_exits_zero(log_dir):
    (log_dir / "usage.db").mkdir()
    done = run_cli(str(log_dir), "record", "hook", "a")
    assert done.returncode == 0 and done.stderr.count("\n") == 1


def test_cli_usage_error(log_dir):
    assert run_cli(str(log_dir), "bogus").returncode == 2


WRITER = (
    "import usage_log, sys\n"
    "for i in range(200):\n"
    "    usage_log.record('hook', 'p' + sys.argv[1], i=i)\n"
)


def test_concurrent_writers_lose_nothing(log_dir):
    env = {**os.environ, "PYTHONPATH": SRC, "ODOO_DEV_HOME": str(log_dir)}
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
