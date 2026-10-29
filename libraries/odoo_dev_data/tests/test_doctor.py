from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from odoo_dev_data import doctor as doctor_mod
from odoo_dev_data.db import MIGRATIONS, connect
from odoo_dev_data.doctor import doctor, main, render


def by_name(report):
    return {check.name: check for check in report.checks}


def test_missing_home_fails_and_creates_nothing(tmp_path):
    home = tmp_path / "absent"
    report = doctor(home, env={})
    assert not report.ok
    assert by_name(report)["home"].status == "fail"
    assert not home.exists()
    text = render(report)
    assert "ODOO_DEV_HOME=" in text
    assert "1 problem" in text


def test_empty_home_is_read_only_and_ok(tmp_path):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    home.chmod(0o700)
    report = doctor(home, env={})
    checks = by_name(report)
    assert report.ok
    assert report.schema_version is None
    assert checks["home mode"].status == "ok"
    for name in ("config.toml", "repo-map.json", "state.db", "usage.db"):
        assert checks[name].status == "note"
    assert checks["tasks/"].status == "note"
    assert checks["locks/"].status == "note"
    assert list(home.iterdir()) == []


def test_group_readable_home_is_a_note(tmp_path):
    tmp_path.chmod(0o750)
    assert by_name(doctor(tmp_path, env={}))["home mode"].status == "note"


def test_populated_home(tmp_path):
    connect(tmp_path).close()
    (tmp_path / "config.toml").write_text(
        '[connection]\nurl = "https://x"\ndb = "d"\nusername = "u"\npassword = "p"\n'
    )
    projects = {"a": {}, "b": {}}
    (tmp_path / "repo-map.json").write_text(json.dumps({"projects": projects}))
    report = doctor(tmp_path, env={})
    checks = by_name(report)
    assert report.ok
    assert report.schema_version == len(MIGRATIONS)
    assert checks["state.db"].status == "ok"
    assert "events=0" in checks["state.db"].detail
    assert "jobs=0" in checks["state.db"].detail
    detail = checks["config.toml"].detail
    assert detail == "url=https://x db=d username=u"
    assert "password" not in detail
    assert checks["repo-map.json"].detail == "2 projects"
    assert checks["tasks/"].status == "ok"
    assert checks["locks/"].status == "ok"


def test_config_without_url_is_a_note(tmp_path):
    (tmp_path / "config.toml").write_text("[behavior]\n")
    check = by_name(doctor(tmp_path, env={}))["config.toml"]
    assert (check.status, check.detail) == ("note", "no url resolved")


def test_github_account_unset_is_a_note(tmp_path):
    check = by_name(doctor(tmp_path, env={}))["github account"]
    assert check.status == "note"
    assert "[github] account unset" in check.detail


@pytest.mark.parametrize(("code", "status"), [(0, "ok"), (1, "fail")])
def test_github_account_token_check(tmp_path, monkeypatch, code, status):
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, code, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    check = by_name(doctor(tmp_path, env={"ODOO_GITHUB_ACCOUNT": "work"}))[
        "github account"
    ]
    assert check.status == status
    assert "work" in check.detail
    assert calls == [["gh", "auth", "token", "--user", "work"]]


def test_broken_files_fail(tmp_path):
    (tmp_path / "config.toml").write_text("= not toml [")
    (tmp_path / "repo-map.json").write_text("{nope")
    (tmp_path / "state.db").write_text("this is not a database")
    report = doctor(tmp_path, env={})
    checks = by_name(report)
    assert not report.ok
    assert checks["config.toml"].status == "fail"
    assert checks["repo-map.json"].status == "fail"
    assert checks["state.db"].status == "fail"
    assert report.schema_version is None


def test_schema_ahead_fails(tmp_path):
    conn = connect(tmp_path)
    conn.execute(
        "INSERT INTO schema_migrations (version, name, applied_us) VALUES (99, 'x', 1)"
    )
    conn.close()
    report = doctor(tmp_path, env={})
    check = by_name(report)["state.db"]
    assert check.status == "fail"
    assert "schema ahead" in check.detail
    assert report.schema_version == 99


def test_main_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ODOO_DEV_HOME", str(tmp_path))
    code = main(["doctor", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert set(data) == {"home", "env_set", "checks", "schema_version", "ok"}
    assert data["home"] == str(tmp_path)
    assert data["env_set"] is True
    assert code == (0 if data["ok"] else 1)


def test_main_text_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ODOO_DEV_HOME", str(tmp_path / "absent"))
    assert main(["doctor"]) == 1
    assert "1 problem(s)" in capsys.readouterr().out


def test_python_dash_m_smoke(tmp_path):
    tmp_path.chmod(0o700)
    env = {**os.environ, "ODOO_DEV_HOME": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "-m", "odoo_dev_data", "doctor"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("fstype", "status"), [("fuse.grpcfuse", "note"), ("ext4", "ok"), (None, None)]
)
def test_wal_check(tmp_path, monkeypatch, fstype, status):
    monkeypatch.setattr(doctor_mod, "_mount_fstype", lambda path: fstype)
    checks = by_name(doctor(tmp_path, env={}))
    if status is None:
        assert "wal" not in checks
    else:
        assert checks["wal"].status == status


def test_mount_fstype_reads_real_proc_mounts():
    result = doctor_mod._mount_fstype(Path("/"))
    assert result is None or isinstance(result, str)
