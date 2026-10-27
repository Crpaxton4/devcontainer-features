"""Tests for ``odoo-dev repo-map`` on a temporary ``ODOO_DEV_HOME``."""

import json
from pathlib import Path

import pytest

from odoo_cli.__main__ import main

SET_ACME = [
    "repo-map",
    "set",
    "acme",
    "--repo",
    "acme-odoo",
    "--odoo-version",
    "17.0",
    "--aliases",
    "ac, acm",
    "--branch-flow",
    "task,UAT,main",
    "--upgrade-branch",
    "18.0=18-upgrade",
    "--release-assignee",
    "jo",
]


def _map_file(home: Path) -> Path:
    return home / "repo-map.json"


def _out(capsys: pytest.CaptureFixture) -> str:
    return capsys.readouterr().out


def test_set_writes_the_entry_in_the_new_schema(
    _isolated_odoo_dev_home: Path, capsys: pytest.CaptureFixture
) -> None:
    assert main(SET_ACME) == 0
    assert _out(capsys) == "set acme\n"
    document = json.loads(_map_file(_isolated_odoo_dev_home).read_text("utf-8"))
    assert document["projects"]["acme"] == {
        "repo": "acme-odoo",
        "odoo_version": "17.0",
        "aliases": ["ac", "acm"],
        "branch_flow": ["task", "UAT", "main"],
        "upgrade_branch": {"18.0": "18-upgrade"},
        "release_assignee": "jo",
        "release_reviewer": "",
    }
    assert document["defaults"]["branch_flow"] == ["task", "E2E", "UAT", "main"]


def test_set_replaces_an_existing_entry(
    _isolated_odoo_dev_home: Path, capsys: pytest.CaptureFixture
) -> None:
    main(SET_ACME)
    assert (
        main(["repo-map", "set", "acme", "--repo", "new", "--odoo-version", "18.0"])
        == 0
    )
    document = json.loads(_map_file(_isolated_odoo_dev_home).read_text("utf-8"))
    assert document["projects"]["acme"]["repo"] == "new"
    assert document["projects"]["acme"]["aliases"] == []


def test_invalid_set_exits_4_and_leaves_the_file_untouched(
    _isolated_odoo_dev_home: Path, capsys: pytest.CaptureFixture
) -> None:
    main(SET_ACME)
    before = _map_file(_isolated_odoo_dev_home).read_bytes()
    capsys.readouterr()
    bad = ["repo-map", "set", "bad", "--repo", "r", "--odoo-version", "seventeen"]
    assert main(bad) == 4
    captured = capsys.readouterr()
    assert 'bad: odoo_version "seventeen" must be like 17.0' in captured.err
    assert captured.out == ""
    assert _map_file(_isolated_odoo_dev_home).read_bytes() == before


def test_invalid_set_on_an_empty_home_writes_no_file(
    _isolated_odoo_dev_home: Path, capsys: pytest.CaptureFixture
) -> None:
    bad = ["repo-map", "set", "bad", "--repo", "r", "--odoo-version", "x"]
    assert main(bad) == 4
    assert not _map_file(_isolated_odoo_dev_home).exists()


def test_malformed_upgrade_branch_exits_4(capsys: pytest.CaptureFixture) -> None:
    argv = [*SET_ACME, "--upgrade-branch", "no-equals"]
    assert main(argv) == 4
    assert "SERIES=BRANCH" in capsys.readouterr().err


def test_get_and_list(capsys: pytest.CaptureFixture) -> None:
    main(SET_ACME)
    capsys.readouterr()
    assert main(["repo-map", "list"]) == 0
    assert _out(capsys) == "acme  acme-odoo  17.0  aliases=ac,acm\n"
    assert main(["repo-map", "list", "--json"]) == 0
    assert [p["name"] for p in json.loads(_out(capsys))] == ["acme"]
    assert main(["repo-map", "get", "ac", "--json"]) == 0
    assert json.loads(_out(capsys))["repo"] == "acme-odoo"
    assert main(["repo-map", "get", "nope"]) == 3
    assert "no project 'nope'" in capsys.readouterr().err


def test_list_on_an_empty_map_is_json_array(capsys: pytest.CaptureFixture) -> None:
    assert main(["repo-map", "list", "--json"]) == 0
    assert json.loads(_out(capsys)) == []


def test_resolve_by_name_and_without_a_match(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    repos = tmp_path / "repos"
    (repos / "acme-odoo").mkdir(parents=True)
    main(SET_ACME)
    capsys.readouterr()
    found = ["repo-map", "resolve", "acm", "--repos-dir", str(repos), "--json"]
    assert main(found) == 0
    data = json.loads(_out(capsys))
    assert data["project"]["name"] == "acme"
    assert data["repo_root"] == str(repos / "acme-odoo")
    assert data["warnings"] == []
    assert main(["repo-map", "resolve", "ghost", "--json"]) == 3
    data = json.loads(_out(capsys))
    assert data["project"] is None
    assert data["warnings"] == ["no repo-map entry for ghost"]


def test_validate_reports_errors_with_exit_4(
    _isolated_odoo_dev_home: Path, capsys: pytest.CaptureFixture
) -> None:
    assert main(["repo-map", "validate"]) == 0
    assert _out(capsys) == "ok\n"
    _isolated_odoo_dev_home.mkdir()
    _map_file(_isolated_odoo_dev_home).write_text(
        json.dumps({"projects": {"p": {"repo": "r", "odoo_version": "x"}}}), "utf-8"
    )
    assert main(["repo-map", "validate", "--json"]) == 4
    report = json.loads(_out(capsys))
    assert report["ok"] is False
    assert report["errors"] == ['p: odoo_version "x" must be like 17.0']


def test_remove(_isolated_odoo_dev_home: Path, capsys: pytest.CaptureFixture) -> None:
    main(SET_ACME)
    capsys.readouterr()
    assert main(["repo-map", "remove", "acme"]) == 0
    assert _out(capsys) == "removed acme\n"
    document = json.loads(_map_file(_isolated_odoo_dev_home).read_text("utf-8"))
    assert document["projects"] == {}
    assert main(["repo-map", "remove", "acme"]) == 3


def test_defaults_show_and_set(
    _isolated_odoo_dev_home: Path, capsys: pytest.CaptureFixture
) -> None:
    assert main(["repo-map", "defaults", "--json"]) == 0
    assert json.loads(_out(capsys))["branch_flow"] == ["task", "E2E", "UAT", "main"]
    assert not _map_file(_isolated_odoo_dev_home).exists()
    set_args = [
        "repo-map",
        "defaults",
        "--branch-flow",
        "task,main",
        "--release-reviewer",
        "kim",
    ]
    assert main([*set_args, "--json"]) == 0
    shown = json.loads(_out(capsys))
    assert shown == {
        "branch_flow": ["task", "main"],
        "release_assignee": "",
        "release_reviewer": "kim",
    }
    document = json.loads(_map_file(_isolated_odoo_dev_home).read_text("utf-8"))
    assert document["defaults"] == shown


def test_invalid_defaults_exit_4(capsys: pytest.CaptureFixture) -> None:
    assert main(["repo-map", "defaults", "--branch-flow", "main"]) == 4
    assert "defaults: branch_flow needs at least a source and a target" in (
        capsys.readouterr().err
    )
