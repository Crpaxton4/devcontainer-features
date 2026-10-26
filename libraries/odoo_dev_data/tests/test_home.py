from pathlib import Path

import pytest

from odoo_dev_data import home as h


def test_env_wins_over_default(tmp_path):
    assert h.resolve_home({h.ENV_HOME: str(tmp_path)}) == tmp_path


def test_empty_env_falls_back_to_default():
    assert h.resolve_home({h.ENV_HOME: ""}) == Path(h.DEFAULT_HOME).expanduser()
    assert h.resolve_home({}) == Path(h.DEFAULT_HOME).expanduser()


def test_default_expands_tilde(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv(h.ENV_HOME, raising=False)
    assert h.resolve_home() == tmp_path / ".config" / "odoo-dev"


def test_paths_layout(tmp_path):
    p = h.paths(tmp_path)
    assert p.home == tmp_path
    assert p.config_toml == tmp_path / "config.toml"
    assert p.repo_map == tmp_path / "repo-map.json"
    assert p.state_db == tmp_path / "state.db"
    assert p.usage_db == tmp_path / "usage.db"
    assert p.tasks == tmp_path / "tasks"
    assert p.locks == tmp_path / "locks"


def test_paths_default_uses_env(monkeypatch, tmp_path):
    monkeypatch.setenv(h.ENV_HOME, str(tmp_path))
    assert h.paths().home == tmp_path


def test_ensure_home_creates_with_mode(tmp_path):
    root = tmp_path / "a" / "home"
    p = h.ensure_home(root)
    assert p == h.paths(root)
    assert p.tasks.is_dir() and p.locks.is_dir()
    assert root.stat().st_mode & 0o777 == 0o700


def test_ensure_home_second_call_keeps_mode(tmp_path):
    root = tmp_path / "home"
    root.mkdir()
    root.chmod(0o755)
    h.ensure_home(root)
    assert root.stat().st_mode & 0o777 == 0o755
    h.ensure_home(root)
    assert root.stat().st_mode & 0o777 == 0o755


def test_task_dir_accepts_int_and_str(tmp_path):
    expected = tmp_path / "tasks" / "123"
    assert h.task_dir(123, tmp_path) == expected
    assert h.task_dir("123", tmp_path) == expected
    assert not expected.exists()


@pytest.mark.parametrize("bad", ["../x", "12a", ""])
def test_task_dir_rejects(bad, tmp_path):
    with pytest.raises(ValueError, match="task id"):
        h.task_dir(bad, tmp_path)


def test_job_bundle_dir(tmp_path):
    got = h.job_bundle_dir(7, "job-1.a_b", tmp_path)
    assert got == tmp_path / "tasks" / "7" / "jobs" / "job-1.a_b"


@pytest.mark.parametrize("bad", ["a/b", ".hidden", "..", ""])
def test_job_bundle_dir_rejects_job(bad, tmp_path):
    with pytest.raises(ValueError, match="job id"):
        h.job_bundle_dir(7, bad, tmp_path)


def test_job_bundle_dir_rejects_task(tmp_path):
    with pytest.raises(ValueError, match="task id"):
        h.job_bundle_dir("x", "j", tmp_path)
