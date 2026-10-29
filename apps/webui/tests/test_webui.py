import json

import pytest


def test_jobs_page_lists_every_job_with_its_figures(client):
    html = client.get("/jobs").get_data(as_text=True)
    assert "<html" in html and 'hx-trigger="every 3s"' in html
    for text in ("j-pending", "j-running", "j-done", "j-dead", "COMPLETED", "1.25"):
        assert text in html
    assert html.index("j-dead") < html.index("j-pending")  # newest first


def test_jobs_fragment_for_htmx(client):
    response = client.get("/jobs", headers={"HX-Request": "true"})
    html = response.get_data(as_text=True)
    assert "<html" not in html and 'id="jobs-table"' in html
    assert response.headers["Vary"] == "HX-Request"


def test_job_detail_shows_entry_structured_output_and_files(client):
    html = client.get("/jobs/j-done").get_data(as_text=True)
    for text in ("COMPLETED", "question_count", "&#34;summary&#34;", "ok"):
        assert text in html
    assert "/jobs/j-done/files/envelope.json" in html
    assert "/jobs/j-done/files/session.jsonl" in html
    assert "files/STOP" not in html


def test_job_detail_without_bundle(client):
    html = client.get("/jobs/j-pending").get_data(as_text=True)
    assert "No structured output" in html and "No bundle file" in html


def test_unknown_job_is_404(client):
    assert client.get("/jobs/nope").status_code == 404


def test_bundle_file_is_shown(client):
    response = client.get("/jobs/j-done/files/envelope.json")
    assert response.status_code == 200 and response.mimetype == "text/plain"
    assert "result" in response.get_data(as_text=True)


@pytest.mark.parametrize(
    "url",
    [
        "/jobs/j-done/files/../secret.txt",
        "/jobs/j-done/files/%2e%2e/secret.txt",
        "/jobs/j-done/files/%2e%2e%2fsecret.txt",
        "/jobs/j-done/files/..%5csecret.txt",
        "/jobs/j-done/files//etc/passwd",
        "/jobs/j-done/files/missing.json",
        "/jobs/j-pending/files/envelope.json",
    ],
)
def test_bundle_path_outside_the_bundle_is_404(client, url):
    assert client.get(url, follow_redirects=True).status_code == 404


def test_bundle_symlink_out_is_404(client, tmp_path):
    (tmp_path / "bundle" / "link.txt").symlink_to(tmp_path / "secret.txt")
    assert client.get("/jobs/j-done/files/link.txt").status_code == 404


def test_queue_lists_pending_running_and_batches(client):
    html = client.get("/queue").get_data(as_text=True)
    assert "j-pending" in html and "j-running" in html
    assert "j-done" not in html


def test_queue_lists_batches_waiting_for_a_report(client, home):
    from odoo_dev_data.jobs import DeadReason, JobStore

    with JobStore(home=home) as store:
        store.dead_letter("j-pending", DeadReason.KILLED_BY_SIGNAL)
        store.dead_letter("j-running", DeadReason.KILLED_BY_SIGNAL)
    assert "<li>b1</li>" in client.get("/queue").get_data(as_text=True)


def test_friction_page_groups_by_task(client):
    html = client.get("/friction").get_data(as_text=True)
    for text in ("Task 300", "no diff", "retry", "j-done"):
        assert text in html


def test_usage_page_with_and_without_usage_db(client, usage_db):
    html = client.get("/usage").get_data(as_text=True)
    assert "odoo-dev:plan" in html and "implement" in html
    only = client.get("/usage?kind=job").get_data(as_text=True)
    assert "implement" in only and "odoo-dev:plan" not in only
    assert "<html" not in client.get("/usage", headers={"HX-Request": "true"}).get_data(
        as_text=True
    )


def test_usage_page_without_usage_db(client):
    assert "No usage line" in client.get("/usage").get_data(as_text=True)


def test_usage_bad_days_is_400(client):
    assert client.get("/usage?days=x").status_code == 400
    assert client.get("/usage?days=-1").status_code == 400


@pytest.mark.parametrize("url", ["/", "/jobs", "/queue", "/friction", "/usage"])
def test_empty_state_renders(empty_client, url):
    assert empty_client.get(url, follow_redirects=True).status_code == 200


def test_empty_state_texts(empty_client):
    assert "No job exists" in empty_client.get("/jobs").get_data(as_text=True)
    assert "No friction entry" in empty_client.get("/friction").get_data(as_text=True)


def test_only_get_routes():
    from webui import create_app

    for rule in create_app().url_map.iter_rules():
        assert rule.methods - {"GET", "HEAD", "OPTIONS"} == set(), rule.rule


def test_post_is_refused(client):
    assert client.post("/jobs").status_code == 405


def test_transcript_fragment_renders_the_tail(client, claude_home):
    lines = [
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "hello from claude"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls " + "x" * 300}},
        ]}},
        "{not json",
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "content": "y" * 500},
        ]}},
        {"type": "summary"},
    ]  # fmt: skip
    text = "\n".join(l if isinstance(l, str) else json.dumps(l) for l in lines)
    (claude_home / "projects" / "slug" / "sess-1.jsonl").write_text(text)
    html = client.get("/jobs/j-running/transcript").get_data(as_text=True)
    assert "hello from claude" in html and "[Bash]" in html
    assert "x" * 150 in html and "x" * 300 not in html
    assert "y" * 400 in html and "y" * 401 not in html
    assert "Waiting" not in html


def test_transcript_waits_for_the_session(client):
    for job_id in ("j-pending", "j-running"):  # no session id / no file yet
        html = client.get(f"/jobs/{job_id}/transcript").get_data(as_text=True)
        assert "Waiting for the session transcript" in html
    assert client.get("/jobs/nope/transcript").status_code == 404


def test_detail_polls_the_transcript_only_while_running(client):
    running = client.get("/jobs/j-running").get_data(as_text=True)
    done = client.get("/jobs/j-done").get_data(as_text=True)
    assert 'hx-trigger="load, every 2s"' in running
    assert 'hx-trigger="load"' in done and "every 2s" not in done


def test_htmx_is_vendored(client):
    response = client.get("/static/htmx-2.0.4.min.js")
    assert response.status_code == 200 and b"2.0.4" in response.data
