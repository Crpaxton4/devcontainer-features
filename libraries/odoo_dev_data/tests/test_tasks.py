import json
import threading

import pytest

from odoo_dev_data import tasks as t


def entry(**over):
    base = {"tool": "x", "expected": "e", "observed": "o", "did": "d"}
    return {**base, **over}


def test_read_missing_is_empty(tmp_path):
    rec = t.read_task(5, tmp_path)
    assert rec == t.TaskRecord(task_id="5")


def test_round_trip(tmp_path):
    rec = t.TaskRecord("5", {"plan": {"a": 1}, "build": {"b": [2]}}, [entry()])
    path = t.write_task(rec, tmp_path)
    assert path == tmp_path / "tasks" / "5" / "task.json"
    assert path.read_text().endswith("\n")
    assert t.read_task(5, tmp_path) == rec


def test_non_object_raises(tmp_path):
    t.write_task(t.TaskRecord("5"), tmp_path)
    path = t.task_json_path(5, tmp_path)
    path.write_text("[1]")
    with pytest.raises(ValueError, match="task.json"):
        t.read_task(5, tmp_path)


@pytest.mark.parametrize("bad", ['"x"', "[1]"])
def test_bad_friction_raises(tmp_path, bad):
    t.write_task(t.TaskRecord("5"), tmp_path)
    t.task_json_path(5, tmp_path).write_text('{"friction": %s}' % bad)
    with pytest.raises(ValueError, match="friction"):
        t.read_task(5, tmp_path)


def test_legacy_directory_reads_empty(tmp_path):
    legacy = tmp_path / "tasks" / "31508"
    legacy.mkdir(parents=True)
    (legacy / "10-env.json").write_text("{}")
    assert t.read_task(31508, tmp_path).sections == {}


def test_append_friction_threads(tmp_path):
    def work(n):
        for i in range(5):
            t.append_friction(9, [entry(did=f"{n}-{i}")], job_id="j", home=tmp_path)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    doc = json.loads(t.task_json_path(9, tmp_path).read_text())
    assert len(doc["friction"]) == 40
    assert {e["job_id"] for e in doc["friction"]} == {"j"}
    assert "recorded_at" in doc["friction"][0]


def test_append_friction_returns_count_and_overwrites_stamps(tmp_path):
    assert t.append_friction(9, [entry(job_id="evil")], job_id="j", home=tmp_path) == 1
    assert t.append_friction(9, [entry(), entry()], home=tmp_path) == 3
    got = t.read_task(9, tmp_path).friction
    assert got[0]["job_id"] == "j"
    assert got[1]["job_id"] == ""


def test_append_friction_invalid_writes_nothing(tmp_path):
    bad = {"tool": "x", "expected": "e", "did": "d"}
    with pytest.raises(ValueError, match="observed"):
        t.append_friction(9, [entry(), bad], home=tmp_path)
    assert not t.task_json_path(9, tmp_path).exists()
    with pytest.raises(ValueError, match="tool"):
        t.append_friction(9, [entry(tool=3)], home=tmp_path)


def test_no_temp_file_left(tmp_path):
    t.append_friction(9, [entry()], home=tmp_path)
    t.write_task(t.TaskRecord("9", {"plan": {}}), tmp_path)
    names = sorted(p.name for p in (tmp_path / "tasks" / "9").iterdir())
    assert names == ["task.json"]


def test_next_draft_path_numbers(tmp_path):
    assert t.draft_paths(4, tmp_path) == []
    first = t.next_draft_path(4, tmp_path)
    second = t.next_draft_path(4, tmp_path)
    assert (first.name, second.name) == ("draft-1.md", "draft-2.md")
    assert first.exists()
    (first.parent / "draft-7.md").write_text("x")
    assert t.next_draft_path(4, tmp_path).name == "draft-8.md"
    names = [p.name for p in t.draft_paths(4, tmp_path)]
    assert names == ["draft-1.md", "draft-2.md", "draft-7.md", "draft-8.md"]


def test_draft_paths_sorts_numerically(tmp_path):
    d = tmp_path / "tasks" / "4"
    d.mkdir(parents=True)
    for name in ("draft-10.md", "draft-2.md", "draft-x.md", "notes.md"):
        (d / name).write_text("x")
    assert [p.name for p in t.draft_paths(4, tmp_path)] == ["draft-2.md", "draft-10.md"]


def test_approve_draft(tmp_path):
    draft = t.next_draft_path(4, tmp_path)
    draft.write_bytes(b"# brief\n\xc3\xa9\n")
    brief = t.approve_draft(4, draft, tmp_path)
    assert brief == t.brief_path(4, tmp_path)
    assert brief.read_bytes() == draft.read_bytes()
    assert sorted(p.name for p in brief.parent.iterdir()) == ["brief.md", "draft-1.md"]


def test_approve_rejects_empty_and_foreign(tmp_path):
    draft = t.next_draft_path(4, tmp_path)
    with pytest.raises(ValueError, match="empty"):
        t.approve_draft(4, draft, tmp_path)
    outside = tmp_path / "draft-1.md"
    outside.write_text("x")
    with pytest.raises(ValueError, match="not a draft"):
        t.approve_draft(4, outside, tmp_path)
    assert not t.brief_path(4, tmp_path).exists()
