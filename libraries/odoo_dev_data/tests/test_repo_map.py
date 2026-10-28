"""Tests for ``odoo_dev_data.repo_map``."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from odoo_dev_data.repo_map import (
    Defaults,
    Project,
    RepoMap,
    from_json,
    load_repo_map,
    resolve_project,
    save_repo_map,
    to_json,
    validate,
)

LEGACY = {
    "_doc": "legacy",
    "projects": {
        "acme": {
            "repo": "acme-odoo",
            "repo_path": "/srv/checkouts/acme-odoo",
            "default_branch": "main",
            "odoo_version": "17.0",
            "branch_flow": [":task", "UAT", "main"],
            "flow_confirmed": True,
            "remote": "acme-org/acme-odoo",
            "notes": "hello",
            "release_assignee": "cp",
            "somethingnew": 1,
        },
        "beta": {
            "repo": "beta",
            "default_branch": "main",
            "odoo_version": "18.0",
            "aliases": ["b"],
            "upgrade_branch": {"19.0": "19-upgrade"},
        },
        "gamma": {
            "repo": "gamma",
            "odoo_version": "16.0",
            "branch_flow": ["dev", "main"],
        },
    },
}


def make_map() -> RepoMap:
    return from_json(json.dumps(LEGACY))


def test_from_json_legacy():
    repo_map = make_map()
    acme = repo_map.projects["acme"]
    assert acme.branch_flow == ("task", "UAT", "main")
    assert acme.repo == "/srv/checkouts/acme-odoo"
    assert acme.repo_is_absolute and acme.repo_name == "acme-odoo"
    assert repo_map.projects["beta"].aliases == ("b",)
    assert repo_map.projects["gamma"].branch_flow == ("dev", "main")
    out = json.loads(to_json(repo_map))
    for key in ("repo_path", "default_branch", "flow_confirmed", "remote", "notes"):
        assert key not in out["projects"]["acme"]
    assert "_doc" not in out and "somethingnew" not in out["projects"]["acme"]


def test_round_trip():
    repo_map = make_map()
    text = to_json(repo_map)
    assert text.endswith("\n")
    assert from_json(text) == repo_map
    assert to_json(from_json(text)) == text


@pytest.mark.parametrize("text", ["[]", '{"projects": []}', '"x"'])
def test_from_json_rejects_bad_shape(text):
    with pytest.raises(ValueError):
        from_json(text)


def test_load_missing_file(tmp_path):
    assert load_repo_map(tmp_path / "nope.json") == RepoMap()
    assert load_repo_map(home=tmp_path) == RepoMap()


def test_load_from_home(tmp_path):
    (tmp_path / "repo-map.json").write_text(json.dumps(LEGACY))
    assert set(load_repo_map(home=tmp_path).projects) == {"acme", "beta", "gamma"}


def test_flow_and_branches():
    repo_map = make_map()
    assert repo_map.flow("beta") == ("task", "E2E", "UAT", "main")
    assert repo_map.flow("acme") == ("task", "UAT", "main")
    assert repo_map.default_branch("beta") == "main"
    assert repo_map.next_branch("beta", "task") == "E2E"
    assert repo_map.next_branch("beta", "task", {"UAT", "main"}) == "UAT"
    assert repo_map.next_branch("beta", "E2E", {"main"}) == "main"
    assert repo_map.next_branch("beta", "main") is None
    assert repo_map.next_branch("beta", "nope") is None
    assert repo_map.next_branch("beta", "UAT", {"E2E"}) is None
    assert repo_map.base_branch("beta") == "E2E"
    assert repo_map.base_branch("beta", {"main"}) == "main"
    assert repo_map.base_branch("acme") == "UAT"


def test_task_never_returned():
    repo_map = RepoMap(projects={"p": Project("p", "p", "17.0")})
    flow = repo_map.flow("p")
    assert all(repo_map.next_branch("p", b) != "task" for b in flow)


def test_release_and_upgrade_fallbacks():
    repo_map = make_map()
    repo_map.defaults = Defaults(release_assignee="d", release_reviewer="r")
    assert repo_map.release_assignee("acme") == "cp"
    assert repo_map.release_assignee("beta") == "d"
    assert repo_map.release_reviewer("beta") == "r"
    assert repo_map.upgrade_branch("beta", "19.0") == "19-upgrade"
    assert repo_map.upgrade_branch("beta", "20.0") == "main"


def test_find():
    repo_map = make_map()
    assert repo_map.find("acme").name == "acme"
    assert repo_map.find("b").name == "beta"
    assert repo_map.find("ACME").name == "acme"
    assert repo_map.find("B").name == "beta"
    assert repo_map.find("zzz") is None
    repo_map.projects["Acme"] = Project("Acme", "x", "17.0")
    assert repo_map.find("acme").name == "acme"


def base(**kw) -> RepoMap:
    other = kw.pop("other", None)
    fields = {"name": "p", "repo": "p", "odoo_version": "17.0"} | kw
    projects = {fields["name"]: Project(**fields)}
    if other:
        projects[other.name] = other
    return RepoMap(projects=projects)


def test_validate_ok():
    assert validate(make_map()) == []


@pytest.mark.parametrize(
    "repo_map, expected",
    [
        (base(name=" "), "empty project name"),
        (base(repo=" "), "repo is required"),
        (base(odoo_version="17"), "odoo_version"),
        (base(branch_flow=("main",)), "at least a source and a target"),
        (base(branch_flow=("a", "task")), "first element"),
        (base(branch_flow=("task", "a", "a")), "repeats"),
        (base(branch_flow=("task", "-a")), "forbids"),
        (base(branch_flow=("task", "a..b")), "not a legal"),
        (base(branch_flow=("task", "a b")), "forbids"),
        (base(branch_flow=("task", "a~")), "forbids"),
        (base(branch_flow=("task", "a/")), "not a legal"),
        (base(branch_flow=("task", "x.lock")), "not a legal"),
        (base(branch_flow=("task", "a@{b")), "not a legal"),
        (base(aliases=("",)), "non-empty"),
        (base(aliases=("p",)), "also the name"),
        (
            base(aliases=("x",), other=Project("q", "q", "17.0", aliases=("x",))),
            "already an alias",
        ),
        (base(upgrade_branch={"19": "x"}), "must be like 19.0"),
        (base(upgrade_branch={"19.0": "a b"}), "not a legal"),
        (RepoMap(defaults=Defaults(branch_flow=("main",))), "defaults:"),
        (RepoMap(defaults=Defaults(branch_flow=("task", "a b"))), "defaults:"),
    ],
)
def test_validate_rules(repo_map, expected):
    errors = validate(repo_map)
    assert errors and any(expected in e for e in errors), errors


def test_save_writes_and_keeps_backup(tmp_path):
    path = tmp_path / "repo-map.json"
    repo_map = make_map()
    assert save_repo_map(repo_map, path) == path
    assert load_repo_map(path) == repo_map
    assert not path.with_name("repo-map.json.bak").exists()
    first = path.read_text()
    repo_map.projects["beta"] = Project("beta", "beta2", "18.0")
    save_repo_map(repo_map, path)
    assert path.with_name("repo-map.json.bak").read_text() == first
    assert load_repo_map(path).projects["beta"].repo == "beta2"
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "repo-map.json",
        "repo-map.json.bak",
    ]


def test_save_refuses_invalid(tmp_path):
    path = tmp_path / "repo-map.json"
    path.write_text("old")
    with pytest.raises(ValueError, match="odoo_version"):
        save_repo_map(base(odoo_version="x"), path)
    assert path.read_text() == "old"
    assert not path.with_name("repo-map.json.bak").exists()


def test_save_missing_parent(tmp_path):
    with pytest.raises(FileNotFoundError):
        save_repo_map(RepoMap(), tmp_path / "missing" / "repo-map.json")
    with pytest.raises(FileNotFoundError):
        save_repo_map(RepoMap(), home=tmp_path / "missing")


# --- resolve_project ---------------------------------------------------------


def git_repo(path: Path, origin: str | None) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if origin:
        subprocess.run(
            ["git", "-C", str(path), "remote", "add", "origin", origin], check=True
        )
    return path


def test_resolve_by_name_with_repos_dir(tmp_path):
    repos = tmp_path / "repos"
    git_repo(repos / "acme-odoo", "https://github.com/o/acme-odoo.git")
    repo_map = RepoMap(
        projects={"acme": Project("acme", "acme-odoo", "17.0", aliases=("ac",))}
    )
    result = resolve_project("ac", cwd=tmp_path, repos_dir=repos, repo_map=repo_map)
    assert result.project.name == "acme"
    assert result.repo_root == repos / "acme-odoo"
    assert result.warnings == ()


def test_resolve_by_cwd_origin(tmp_path):
    checkout = git_repo(tmp_path / "wt-123", "git@github.com:o/acme-odoo.git")
    repo_map = RepoMap(
        projects={
            "acme": Project("acme", "acme-odoo", "17.0"),
            "other": Project("other", "other", "17.0"),
        }
    )
    result = resolve_project(cwd=checkout, repo_map=repo_map)
    assert result.project.name == "acme"
    assert result.origin == "o/acme-odoo"
    assert result.repo_root == checkout


def test_resolve_ssh_url_origin(tmp_path):
    checkout = git_repo(tmp_path / "x", "ssh://git@github.com/o/acme-odoo")
    result = resolve_project(cwd=checkout, repo_map=RepoMap())
    assert result.origin == "o/acme-odoo"


def test_resolve_query_falls_back_to_repo_name(tmp_path):
    repo_map = RepoMap(projects={"acme": Project("acme", "acme-odoo", "17.0")})
    result = resolve_project("acme-odoo", cwd=tmp_path, repo_map=repo_map)
    assert result.project.name == "acme"


def test_resolve_ambiguous(tmp_path):
    checkout = git_repo(tmp_path / "c", "https://github.com/o/shared")
    repo_map = RepoMap(
        projects={
            "a17": Project("a17", "shared", "17.0"),
            "a18": Project("a18", "shared", "18.0"),
        }
    )
    result = resolve_project(cwd=checkout, repo_map=repo_map)
    assert result.project is None
    assert result.warnings == ("ambiguous: a17, a18; name the project",)
    assert resolve_project("a18", cwd=checkout, repo_map=repo_map).project.name == "a18"


def test_resolve_no_entry(tmp_path):
    checkout = git_repo(tmp_path / "c", "https://github.com/o/unknown")
    result = resolve_project(cwd=checkout, repo_map=RepoMap())
    assert result.project is None
    assert result.repo_root == checkout
    assert result.warnings == ("no repo-map entry for unknown",)


def test_resolve_shared_absolute_checkout(tmp_path):
    shared = git_repo(tmp_path / "shared", "https://github.com/o/shared.git")
    repo_map = RepoMap(
        projects={
            "a17": Project("a17", str(shared), "17.0"),
            "a18": Project("a18", str(shared), "18.0"),
        }
    )
    for name in ("a17", "a18"):
        result = resolve_project(name, cwd=tmp_path, repo_map=repo_map)
        assert result.project.name == name
        assert result.repo_root == shared
        assert result.warnings == ()


def test_resolve_absolute_repo_with_other_origin_ignored(tmp_path):
    wrong = git_repo(tmp_path / "acme-odoo", "https://github.com/o/something-else")
    repo_map = RepoMap(projects={"acme": Project("acme", str(wrong), "17.0")})
    result = resolve_project("acme", cwd=tmp_path, repo_map=repo_map)
    assert result.project.name == "acme"
    assert result.repo_root is None
    assert result.warnings == (
        f"{wrong} is checked out from o/something-else, not acme-odoo; ignored",
    )


def test_resolve_ignored_absolute_falls_through_to_repos_dir(tmp_path):
    wrong = git_repo(tmp_path / "elsewhere" / "acme", "https://github.com/o/nope")
    repos = tmp_path / "repos"
    (repos / "acme").mkdir(parents=True)
    repo_map = RepoMap(projects={"acme": Project("acme", str(wrong), "17.0")})
    result = resolve_project("acme", cwd=tmp_path, repos_dir=repos, repo_map=repo_map)
    assert result.repo_root == repos / "acme"
    assert len(result.warnings) == 1
