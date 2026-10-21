import sqlite3

import pytest

from webui import create_app, paths
from webui.db import rows


def test_paths_precedence(monkeypatch, tmp_path):
    for name in ("ODOO_DISPATCH_STATE_DIR", "ODOO_TASK_TRACKER_DIR", "XDG_STATE_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.state_root() == tmp_path / ".local/state/odoo-task-tracker"
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert paths.state_root() == tmp_path / "xdg/odoo-task-tracker"
    monkeypatch.setenv("ODOO_TASK_TRACKER_DIR", str(tmp_path / "tracker"))
    assert paths.state_root() == tmp_path / "tracker"
    monkeypatch.setenv("ODOO_DISPATCH_STATE_DIR", str(tmp_path / "dispatch"))
    assert paths.state_root() == tmp_path / "dispatch"
    assert paths.jobs_db() == tmp_path / "dispatch/jobs.db"
    assert paths.runs_db() == tmp_path / "dispatch/runs.db"


def test_artifacts_and_orchestrator_defaults(monkeypatch, tmp_path):
    for name in ("DISPATCH_ARTIFACTS_DIR", "ODOO_ORCHESTRATOR_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.artifacts_root() == tmp_path / ".config/odoo-dev/dispatch/artifacts"
    assert paths.orchestrator_home() == tmp_path / ".local/state/odoo-orchestrator"


def test_rows_is_read_only(state_dir):
    assert rows(state_dir / "jobs.db", "SELECT count(*) AS n FROM jobs") == [{"n": 4}]
    conn = sqlite3.connect(f"file:{state_dir / 'jobs.db'}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM jobs")
    conn.close()


def test_rows_missing_file_and_table(tmp_path):
    assert rows(tmp_path / "absent.db", "SELECT 1") == []
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    assert rows(empty, "SELECT * FROM jobs") == []


def test_jobs_page_lists_every_state(client):
    html = client.get("/jobs").get_data(as_text=True)
    assert "<html" in html
    for text in ("T-100", "T-200", "T-300", "T-400", "deadline", "ctr-200"):
        assert text in html
    for state in ("PENDING (1)", "RUNNING (1)", "DONE (1)", "DEAD_LETTER (1)"):
        assert state in html
    assert 'hx-trigger="every 3s"' in html


def test_jobs_fragment_for_htmx(client):
    response = client.get("/jobs", headers={"HX-Request": "true"})
    html = response.get_data(as_text=True)
    assert "<html" not in html
    assert 'id="jobs-table"' in html
    assert "T-200" in html
    assert response.headers["Vary"] == "HX-Request"


def test_job_detail_shows_run_and_figures(client, artifacts_dir):
    html = client.get("/jobs/j-done").get_data(as_text=True)
    for text in ("COMPLETED", "feat/300", "1.2.3", "111", "222", "333", "444"):
        assert text in html
    for text in ("1.2345", "98765", ">7<"):
        assert text in html
    assert "/jobs/j-done/files/task.json" in html


def test_job_detail_without_run(client):
    html = client.get("/jobs/j-pending").get_data(as_text=True)
    assert "No run is recorded" in html
    assert "No result envelope" in html


def test_unknown_job_is_404(client):
    assert client.get("/jobs/nope").status_code == 404


def test_artifact_file_is_shown(client, artifacts_dir):
    response = client.get("/jobs/j-done/files/task.json")
    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    assert "T-300" in response.get_data(as_text=True)


@pytest.mark.parametrize(
    "url",
    [
        "/jobs/j-done/files/../secret.txt",
        "/jobs/j-done/files/%2e%2e/secret.txt",
        "/jobs/j-done/files/%2e%2e%2fsecret.txt",
        "/jobs/j-done/files//etc/passwd",
        "/jobs/j-done/files/missing.json",
        "/jobs/../files/x",
        "/jobs/%2e%2e/files/secret.txt",
    ],
)
def test_artifact_path_outside_job_dir_is_404(client, artifacts_dir, url):
    assert client.get(url, follow_redirects=True).status_code == 404


def test_artifact_symlink_out_is_404(client, artifacts_dir, tmp_path):
    (artifacts_dir / "link.txt").symlink_to(tmp_path / "artifacts" / "secret.txt")
    assert client.get("/jobs/j-done/files/link.txt").status_code == 404


def test_queue_lists_only_tasks_without_a_job(client, orch_home):
    html = client.get("/queue").get_data(as_text=True)
    assert "T-500" in html and "T-600" in html
    assert "/some/brief.md" in html
    assert "T-100" not in html


def test_queue_without_jobs_db_lists_everything(empty_client):
    home = paths.orchestrator_home()
    (home / "approved").mkdir(parents=True)
    (home / "approved" / "T-9.txt").write_text("/b.md")
    assert "T-9" in empty_client.get("/queue").get_data(as_text=True)


def test_friction_lists_and_shows_both_sources(client, state_dir, orch_home):
    (state_dir / "friction").mkdir()
    (state_dir / "friction" / "2026-01-01.md").write_text("state friction")
    html = client.get("/friction").get_data(as_text=True)
    assert "2026-01-01.md" in html and "2026-01-02.md" in html
    assert "state friction" in client.get("/friction/state/2026-01-01.md").get_data(
        as_text=True
    )
    assert "orchestrator friction" in client.get(
        "/friction/orchestrator/2026-01-02.md"
    ).get_data(as_text=True)


@pytest.mark.parametrize(
    "url",
    [
        "/friction/state/../jobs.db",
        "/friction/state/jobs.db",
        "/friction/other/2026-01-01.md",
        "/friction/state/%2e%2e%2fjobs.md",
    ],
)
def test_friction_bad_path_is_404(client, state_dir, url):
    (state_dir / "friction").mkdir()
    assert client.get(url).status_code == 404


@pytest.mark.parametrize("url", ["/", "/jobs", "/queue", "/friction"])
def test_empty_state_renders(empty_client, url):
    response = empty_client.get(url, follow_redirects=True)
    assert response.status_code == 200


def test_empty_state_texts(empty_client):
    assert "No PENDING jobs" in empty_client.get("/jobs").get_data(as_text=True)
    assert "No friction file" in empty_client.get("/friction").get_data(as_text=True)


def test_only_get_routes():
    app = create_app()
    for rule in app.url_map.iter_rules():
        assert rule.methods - {"GET", "HEAD", "OPTIONS"} == set(), rule.rule


def test_post_is_refused(client):
    assert client.post("/jobs").status_code == 405


def test_jobs_page_does_not_block_a_writer(state_dir, client):
    client.get("/jobs")
    conn = sqlite3.connect(state_dir / "jobs.db", timeout=1)
    conn.execute("UPDATE jobs SET outcome = 'x' WHERE job_id = 'j-done'")
    conn.commit()
    conn.close()


def test_htmx_is_vendored(client):
    response = client.get("/static/htmx-2.0.4.min.js")
    assert response.status_code == 200
    assert b"2.0.4" in response.data
