"""Tests for ``odoo-dev doctor`` on an empty ``ODOO_DEV_HOME``."""

import json
from pathlib import Path

import pytest

from odoo_cli.__main__ import main


@pytest.fixture
def home(_isolated_odoo_dev_home: Path) -> Path:
    _isolated_odoo_dev_home.mkdir(mode=0o700)
    return _isolated_odoo_dev_home


def test_empty_home_reports_ok(home: Path, capsys: pytest.CaptureFixture) -> None:
    assert main(["doctor"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"ODOO_DEV_HOME={home} (from environment)"
    assert any(line.startswith("ok   home") for line in lines)
    assert lines[-1] == "ok"


def test_json_is_the_machine_readable_report(
    home: Path, capsys: pytest.CaptureFixture
) -> None:
    assert main(["doctor", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["home"] == str(home)
    assert {check["name"] for check in report["checks"]} >= {"home", "state.db"}


def test_missing_home_fails_with_exit_1(
    _isolated_odoo_dev_home: Path, capsys: pytest.CaptureFixture
) -> None:
    assert main(["doctor"]) == 1
    assert "1 problem(s)" in capsys.readouterr().out


def test_migrate_dry_run_on_an_empty_home_skips_every_step(
    home: Path, capsys: pytest.CaptureFixture
) -> None:
    assert main(["doctor", "--migrate", "--dry-run", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["dry_run"] is True
    assert report["steps"]
    assert {step["action"] for step in report["steps"]} == {"skipped"}
    assert list(home.iterdir()) == []


def test_dry_run_without_migrate_is_a_usage_error(
    home: Path, capsys: pytest.CaptureFixture
) -> None:
    assert main(["doctor", "--dry-run"]) == 2
    assert "--dry-run needs --migrate" in capsys.readouterr().err
