import json

import pytest
from odoo_dev_data.jobs import JobStore


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


def test_the_only_non_get_route_is_the_answers_post():
    from webui import create_app

    writes = {
        rule.rule
        for rule in create_app().url_map.iter_rules()
        if rule.methods - {"GET", "HEAD", "OPTIONS"}
    }
    assert writes == {"/jobs/<job_id>/answers"}


def test_post_is_refused_on_every_other_route(client):
    assert client.post("/jobs").status_code == 405


def test_job_page_renders_the_question_form_for_a_done_job(client):
    html = client.get("/jobs/j-done").get_data(as_text=True)
    for text in ("Which tax?", "Assumption: VAT", "Reason: unclear", "Accept all"):
        assert text in html
    assert 'action="/jobs/j-done/answers"' in html
    assert 'name="answer_1"' in html


def test_no_form_when_the_job_has_no_questions(client):
    assert "<form" not in client.get("/jobs/j-dead").get_data(as_text=True)


def test_post_saves_written_and_accepted_answers(client, home):
    response = client.post(
        "/jobs/j-done/answers", data={"answer_0": " GST ", "answer_1": ""}
    )
    assert (
        response.status_code == 302 and response.headers["Location"] == "/jobs/j-done"
    )
    with JobStore(home=home) as store:
        assert json.loads(store.get("j-done").answers) == [
            {"question": "Which tax?", "accepted": False, "answer": "GST"},
            {"question": "Which team?", "accepted": True, "answer": ""},
        ]
    html = client.get("/jobs/j-done").get_data(as_text=True)
    assert "GST" in html and "accepted the assumption" in html and "<form" not in html


def test_accept_all_ignores_typed_text(client, home):
    client.post("/jobs/j-done/answers", data={"accept_all": "1", "answer_0": "x"})
    with JobStore(home=home) as store:
        assert all(a["accepted"] for a in json.loads(store.get("j-done").answers))


@pytest.mark.parametrize("job_id", ["j-pending", "j-running", "j-dead"])
def test_post_on_a_job_that_is_not_done_is_refused(client, home, job_id):
    assert client.post(f"/jobs/{job_id}/answers", data={}).status_code == 409
    with JobStore(home=home) as store:
        assert store.get(job_id).answers is None


def test_post_on_an_unknown_job_is_404(client):
    assert client.post("/jobs/nope/answers", data={}).status_code == 404


def test_running_job_streams_the_live_transcript_tail(client, claude_home):
    raw = [f'{{"n": {i}}}' for i in range(100)]
    (claude_home / "projects" / "slug" / "sess-1.jsonl").write_text("\n".join(raw))
    html = client.get("/jobs/j-running/transcript").get_data(as_text=True)
    assert '{"n": 99}' in html.replace("&#34;", '"')
    assert '{"n": 19}' not in html.replace("&#34;", '"')
    assert "No session stream" not in html


def test_finished_job_streams_the_bundle_file(client, claude_home):
    live = claude_home / "projects" / "slug" / "s.jsonl"
    live.write_text('{"live": 1}')  # the live file must not be read after the end
    html = client.get("/jobs/j-done/transcript").get_data(as_text=True)
    assert "line" in html and "live" not in html
    assert "No session stream" not in html


def test_job_without_a_session_file_shows_the_placeholder(client):
    # pending: no session; running: no file yet; dead: no bundle
    for job_id in ("j-pending", "j-running", "j-dead"):
        html = client.get(f"/jobs/{job_id}/transcript").get_data(as_text=True)
        assert "No session stream" in html
    assert client.get("/jobs/nope/transcript").status_code == 404


def test_fragment_polls_only_until_the_job_ends(client):
    for job_id in ("j-pending", "j-running"):
        html = client.get(f"/jobs/{job_id}/transcript").get_data(as_text=True)
        assert 'hx-trigger="every 2s"' in html
    for job_id in ("j-done", "j-dead"):
        html = client.get(f"/jobs/{job_id}/transcript").get_data(as_text=True)
        assert "hx-trigger" not in html


def test_detail_has_the_same_stream_panel_in_every_state(client):
    for job_id in ("j-pending", "j-running", "j-done", "j-dead"):
        html = client.get(f"/jobs/{job_id}").get_data(as_text=True)
        assert 'class="cols"' in html and "<aside>" in html
        assert f'hx-get="/jobs/{job_id}/transcript"' in html


def test_htmx_is_vendored(client):
    response = client.get("/static/htmx-2.0.4.min.js")
    assert response.status_code == 200 and b"2.0.4" in response.data
