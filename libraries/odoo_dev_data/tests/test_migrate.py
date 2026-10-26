from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from odoo_dev_data.config import LocalConfig
from odoo_dev_data.doctor import main, migrate
from odoo_dev_data.migrate import render_migrate

from .legacy_schema import (
    LEGACY_JOBS_DDL,
    LEGACY_RUNS_DDL,
    LEGACY_TRACKER_DDL,
    LEGACY_USER_VERSION,
)

T0 = 1_760_000_000_000_000  # microseconds UTC
BUNDLE_FILES = {
    "envelope.json": '{"outcome": "COMPLETED"}',
    "session.jsonl": '{"type": "x"}\n',
    "harvest.json": "{}",
    "tasks/101/task.json": '{"task_id": 101}',
}


def sha_tree(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def make_tracker(path: Path, version: int = LEGACY_USER_VERSION) -> None:
    with closing(sqlite3.connect(path)) as db:
        db.executescript(LEGACY_TRACKER_DDL)
        for i in (1, 2, 3):
            db.execute(
                "INSERT INTO events (id, source, timestamp, task_ids, external_id) "
                "VALUES (?, 'git', ?, '[12]', ?)",
                (i * 10, f"2026-01-0{i}T10:00:00Z", f"ext-{i}"),
            )
        db.execute(
            "INSERT INTO session_uploads VALUES "
            "('12|1', 55, 1.5, '2026-01-02T00:00:00Z', '12', NULL, NULL)"
        )
        db.execute(
            "INSERT INTO chatter_dedupe VALUES (12, 'k', 99, '2026-01-02T00:00:00Z')"
        )
        db.execute("INSERT INTO settings VALUES ('last_sync', 'yesterday')")
        db.execute(
            "INSERT INTO task_runs (id, task_id, task_name, project_id, project_name,"
            " state, started_at, stopped_at) VALUES "
            "(1, 12, 'Fix it', 1, 'P', 'STOPPED', '2026-01-01T10:00:00Z', "
            "'2026-01-01T11:00:00Z')"
        )
        db.execute(
            "INSERT INTO task_runs (id, task_id, task_name, project_id, project_name,"
            " state, started_at, aborted_at) VALUES "
            "(2, 13, 'Broke it', 1, 'P', 'CLOSED', '2026-01-02 10:00:00', "
            "'2026-01-02 10:30:00')"
        )
        db.execute(f"PRAGMA user_version = {version}")
        db.commit()


def make_jobs(state_dir: Path, bundle: Path, running: bool = False) -> None:
    rows = [
        ("j-done1", "b1", "101", "DONE", "", T0 + 10, T0 + 20, ""),
        ("j-done2", "b2", "102", "DONE", "COMPLETED", T0 + 10, T0 + 20, ""),
        (
            "j-dead",
            "b1",
            "103",
            "DEAD_LETTER",
            "",
            T0 + 10,
            T0 + 20,
            "KILLED_BY_DEADLINE",
        ),
    ]
    if running:
        rows.append(("j-run", "b3", "104", "RUNNING", "", T0 + 10, None, ""))
    with closing(sqlite3.connect(state_dir / "jobs.db")) as db:
        db.executescript(LEGACY_JOBS_DDL)
        for job_id, batch, task, state, outcome, started, ended, reason in rows:
            db.execute(
                "INSERT INTO jobs (job_id, batch_id, task_id, repo, state, brief,"
                " submitted_us, started_us, ended_us, outcome, dead_reason, entry)"
                " VALUES (?, ?, ?, 'acme', ?, 'do it', ?, ?, ?, ?, ?, 'implement')",
                (job_id, batch, task, state, T0, started, ended, outcome, reason),
            )
        db.commit()
    with closing(sqlite3.connect(state_dir / "runs.db")) as db:
        db.executescript(LEGACY_RUNS_DDL)
        db.execute(
            "INSERT INTO runs (session_id, job_id, task_id, repo, branch, pr_num,"
            " started_us, ended_us, outcome, tool_version, transcript) VALUES "
            "('s1', 'j-done1', '101', 'acme', 'task-101', 7, ?, ?, 'COMPLETED',"
            " '1.2.3', '/t/session.jsonl')",
            (T0 + 10, T0 + 20),
        )
        db.execute("INSERT INTO run_envelopes VALUES ('s1', '{\"outcome\": \"ok\"}')")
        db.execute(
            "INSERT INTO run_artifacts VALUES "
            "('j-done1', 's1', ?, 1, 1, 1, 321, ?, '')",
            (str(bundle), T0 + 30),
        )
        db.commit()
    for rel, text in BUNDLE_FILES.items():
        (bundle / rel).parent.mkdir(parents=True, exist_ok=True)
        (bundle / rel).write_text(text)


REPO_MAP = {
    "_doc": "legacy",
    "projects": {
        name: {
            "repo_path": f"/repos/{name}",
            "odoo_version": "17.0",
            "default_branch": "main",
            "flow_confirmed": True,
            "branch_flow": [":task", "UAT", "main"],
            "release_assignee": "carol",
            "release_reviewer": "dave" if name != "c" else "",
        }
        for name in ("a", "b", "c")
    },
}


def build_world(root: Path, *, version: int = 4, running: bool = False) -> dict:
    legacy = root / "legacy"
    state, bundle = legacy / "state", legacy / "artifacts" / "j-done1"
    orch, usage = legacy / "orch", legacy / "usage"
    for directory in (state, bundle, orch / "briefs" / "101", orch / "approved", usage):
        directory.mkdir(parents=True)
    ini = legacy / "sdk.ini"
    ini.write_text(
        "[odoo]\nurl = https://odoo.example\ndb = prod\n\n"
        "[behavior]\nprofiling = true\n\n[model_ids]\nproject.task = 7\n"
    )
    make_tracker(state / "tracker.db", version)
    make_jobs(state, bundle, running)
    for n in (1, 2):
        (orch / "briefs" / "101" / f"draft-{n}.md").write_text(f"draft {n}\n")
    (orch / "approved" / "101.txt").write_text(f"{orch}/briefs/101/draft-2.md\n")
    (orch / "approved" / "legacy-name.txt").write_text("nowhere\n")
    (orch / "handled.txt").write_text("j-dead\nghost\n")
    (orch / "batches-done.txt").write_text("b2\n")
    (usage / "usage.db").write_bytes(b"usage-bytes")
    (usage / "declarations.db").write_bytes(b"decl-bytes")
    home = root / "home"
    home.mkdir()
    (home / "repo-map.json").write_text(json.dumps(REPO_MAP))
    env = {
        "HOME": str(root / "nohome"),
        "ODOO_SDK_CONFIG": str(ini),
        "ODOO_TASK_TRACKER_DIR": str(state),
        "ODOO_DISPATCH_STATE_DIR": str(state),
        "ODOO_ORCHESTRATOR_HOME": str(orch),
        "USAGE_LOG_DIR": str(usage),
    }
    return {"home": home, "env": env, "legacy": legacy}


@pytest.fixture
def world(tmp_path):
    return build_world(tmp_path)


def actions(report) -> dict[str, str]:
    return {s.name: s.action for s in report.steps}


def rows(home: Path, sql: str, *args):
    with closing(sqlite3.connect(home / "state.db")) as db:
        db.row_factory = sqlite3.Row
        return db.execute(sql, args).fetchall()


def test_full_migration(world):
    home, env = world["home"], world["env"]
    before = sha_tree(world["legacy"])
    report = migrate(home, env, now_us=T0 + 99)
    assert report.ok, render_migrate(report)
    assert actions(report) == {
        n: "done"
        for n in (
            "home",
            "config",
            "tracker",
            "jobs",
            "repo-map",
            "orchestrator",
            "usage",
        )
    }
    assert sha_tree(world["legacy"]) == before

    config = LocalConfig.load(path=home / "config.toml", env={})
    assert config.connection["url"] == "https://odoo.example"
    assert config.connection["db"] == "prod"
    assert config.model_id("project.task") == 7
    assert config.profiling is True
    assert (home / "config.toml").stat().st_mode & 0o777 == 0o600

    assert [r["id"] for r in rows(home, "SELECT id FROM events ORDER BY id")] == [
        10,
        20,
        30,
    ]
    assert rows(home, "SELECT session_key FROM session_uploads")[0][0] == "12|1"
    assert rows(home, "SELECT message_id FROM chatter_dedupe")[0][0] == 99
    settings = dict(rows(home, "SELECT key, value FROM settings"))
    assert settings["last_sync"] == "yesterday"
    assert "migrated.tracker" in settings and "migrated.jobs" in settings

    runs = {
        r["job_id"]: r
        for r in rows(home, "SELECT * FROM jobs WHERE job_id LIKE 'run-%'")
    }
    assert runs["run-1"]["outcome"] == "COMPLETED"
    assert runs["run-2"]["outcome"] == "FAILED"
    assert runs["run-1"]["batch_id"] == "legacy-task-runs"
    assert runs["run-1"]["task_id"] == "12" and runs["run-1"]["repo"] == "legacy"
    assert runs["run-1"]["ended_us"] - runs["run-1"]["started_us"] == 3_600_000_000

    jobs = {r["job_id"]: r for r in rows(home, "SELECT * FROM jobs")}
    assert len(jobs) == 5
    done1 = jobs["j-done1"]
    assert (done1["branch"], done1["pr_num"], done1["tool_version"]) == (
        "task-101",
        7,
        "1.2.3",
    )
    assert json.loads(done1["envelope"]) == {"outcome": "ok"}
    assert done1["outcome"] == "COMPLETED"
    assert done1["transcript_path"] == "/t/session.jsonl"
    assert done1["bytes"] == 321 and done1["harvested_us"] == T0 + 30
    assert done1["entry"] == "implement"
    bundle = home / "tasks" / "101" / "jobs" / "j-done1"
    assert done1["bundle_dir"] == str(bundle)
    assert sorted(p.name for p in bundle.iterdir()) == [
        "envelope.json",
        "session.jsonl",
        "task.json",
    ]
    assert jobs["j-dead"]["dead_reason"] == "KILLED_BY_DEADLINE"

    saved = json.loads((home / "repo-map.json").read_text())
    assert saved["defaults"]["release_assignee"] == "carol"
    assert saved["defaults"]["release_reviewer"] == "dave"
    assert saved["projects"]["a"]["repo"] == "/repos/a"
    assert saved["projects"]["a"]["branch_flow"] == ["task", "UAT", "main"]
    assert saved["projects"]["a"]["release_assignee"] == ""
    assert "_doc" not in saved
    assert (home / "repo-map.json.bak").read_text() == json.dumps(REPO_MAP)

    task = home / "tasks" / "101"
    assert (task / "draft-1.md").read_text() == "draft 1\n"
    assert (task / "brief.md").read_text() == "draft 2\n"
    assert (
        "non-numeric"
        in next(s for s in report.steps if s.name == "orchestrator").detail
    )
    assert jobs["j-dead"]["reported_us"] == T0 + 99
    assert jobs["j-done2"]["reported_us"] == T0 + 99
    assert jobs["j-done1"]["reported_us"] is None

    assert (home / "usage.db").read_bytes() == b"usage-bytes"
    assert (home / "declarations.db").read_bytes() == b"decl-bytes"


def test_second_run_skips_everything_and_changes_nothing(world):
    home, env = world["home"], world["env"]
    migrate(home, env)
    snapshot = sha_tree(home)
    report = migrate(home, env)
    assert set(actions(report).values()) == {"skipped"}, render_migrate(report)
    assert sha_tree(home) == snapshot


def test_dry_run_touches_nothing(tmp_path):
    world = build_world(tmp_path)
    home = tmp_path / "fresh"
    report = migrate(home, world["env"], dry_run=True)
    assert report.ok and report.dry_run
    assert not home.exists()
    assert set(actions(report).values()) == {"skipped"}
    assert report.steps[0].detail == "would create"
    assert "would import" in actions_detail(report)["tracker"]
    assert render_migrate(report).splitlines()[0].endswith("(dry run)")


def actions_detail(report) -> dict[str, str]:
    return {s.name: s.detail for s in report.steps}


def test_dry_run_leaves_existing_home_unchanged(world):
    snapshot = sha_tree(world["home"])
    migrate(world["home"], world["env"], dry_run=True)
    assert sha_tree(world["home"]) == snapshot


def test_old_tracker_is_refused(tmp_path):
    world = build_world(tmp_path, version=3)
    report = migrate(world["home"], world["env"])
    step = next(s for s in report.steps if s.name == "tracker")
    assert step.action == "refused"
    assert step.detail == (
        "tracker.db is schema version 3, expected 4; "
        "rebuild it with the legacy tooling first"
    )
    assert not report.ok
    assert render_migrate(report).splitlines()[-1] == "1 refused"


def test_running_job_refuses_the_jobs_step(tmp_path):
    world = build_world(tmp_path, running=True)
    report = migrate(world["home"], world["env"])
    step = next(s for s in report.steps if s.name == "jobs")
    assert step.action == "refused" and "drain the dispatcher" in step.detail
    assert [
        r[0]
        for r in rows(world["home"], "SELECT job_id FROM jobs WHERE job_id LIKE 'j-%'")
    ] == []


def test_invalid_repo_map_is_refused_and_untouched(world):
    path = world["home"] / "repo-map.json"
    bad = json.loads(path.read_text())
    bad["projects"]["a"]["odoo_version"] = "seventeen"
    path.write_text(json.dumps(bad))
    before = path.read_bytes()
    report = migrate(world["home"], world["env"])
    step = next(s for s in report.steps if s.name == "repo-map")
    assert step.action == "refused" and "odoo_version" in step.detail
    assert path.read_bytes() == before
    assert not (world["home"] / "repo-map.json.bak").exists()


def test_toml_config_is_copied_byte_for_byte(tmp_path):
    source = tmp_path / "sdk"
    source.mkdir()
    text = b'[connection]\nurl = "https://x"\n# keep me\n'
    (source / "config.toml").write_bytes(text)
    home = tmp_path / "home"
    report = migrate(home, {"HOME": str(tmp_path), "ODOO_SDK_CONFIG": str(source)})
    assert actions(report)["config"] == "done"
    assert (home / "config.toml").read_bytes() == text


def test_default_locations_follow_home(tmp_path):
    cfg = tmp_path / ".config" / "odoo_sdk"
    cfg.mkdir(parents=True)
    (cfg / "config.ini").write_text("[connection]\nurl = https://y\n")
    report = migrate(tmp_path / "h", {"HOME": str(tmp_path)})
    assert actions(report)["config"] == "done"
    assert actions(report)["tracker"] == "skipped"
    assert (
        LocalConfig.load(path=tmp_path / "h" / "config.toml", env={}).connection["url"]
        == "https://y"
    )


def test_cli_json(world, monkeypatch, capsys):
    monkeypatch.setenv("ODOO_DEV_HOME", str(world["home"]))
    for key, value in world["env"].items():
        monkeypatch.setenv(key, value)
    assert main(["doctor", "--migrate", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["dry_run"] is False
    assert [s["name"] for s in payload["steps"]][0] == "home"
    assert main(["doctor", "--migrate", "--dry-run"]) == 0
    assert "(dry run)" in capsys.readouterr().out
