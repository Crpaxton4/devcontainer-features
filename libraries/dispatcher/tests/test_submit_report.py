"""``dispatch submit`` and ``dispatch report``, and ``ODOO_JOB_ID``."""

import json

import pytest
from odoo_sdk.state.artifacts import ensure_bundle_dir
from odoo_sdk.state.dispatch_state import lock_path
from odoo_sdk.state.jobs import JobState, JobStore

from dispatcher import cli, lifecycle
from dispatcher.lock import DispatcherLock
from support import GOOD, FakeProc, stream
from test_engine_cli import world  # noqa: F401  (fixture)


@pytest.fixture
def submit_env(settings, monkeypatch):
    """The DISPATCH_* env of a dispatcher container, with repo ``qoc`` ready.

    ``settings`` already holds a central config for qoc; this adds the git
    checkout. Request it after ``world``, which makes the same checkout.
    """
    monkeypatch.setenv("DISPATCH_REPOS_DIR", str(settings.repos_dir))
    monkeypatch.setenv("DISPATCH_CONFIG_ROOT", str(settings.config_root))
    monkeypatch.setenv("DISPATCH_INVARIANTS_FILE", settings.invariants_file)
    git = settings.workspace("qoc") / ".git"
    git.mkdir(parents=True, exist_ok=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    return settings


def _arg(tmp_path, *tasks, repo="qoc"):
    brief = tmp_path / "b.md"
    brief.write_text("do it")
    return json.dumps([{"repo": repo, "task": t, "brief": str(brief)} for t in tasks])


def _call(capsys, *argv):
    code = cli.main(list(argv))
    return code, capsys.readouterr().out


def _submit_err(capsys, jobs):
    code = cli.main(["submit", jobs])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _rows():
    store = JobStore()
    try:
        return [(j.task_id, j.state) for s in JobState for j in store.list_by_state(s)]
    finally:
        store.close()


def test_submit_while_the_lock_is_held_writes_pending_rows(
    submit_env, tmp_path, capsys
):
    with DispatcherLock(lock_path()):
        code, out = _call(capsys, "submit", _arg(tmp_path, "1", "2"))
    report = json.loads(out)
    assert code == cli.EXIT_OK and len(report["batch_id"]) == 32
    assert [j["task"] for j in report["jobs"]] == ["1", "2"]
    assert {j["state"] for j in report["jobs"]} == {"PENDING"}
    assert sorted(_rows()) == [("1", JobState.PENDING), ("2", JobState.PENDING)]


def test_a_rejected_array_writes_nothing_and_exits_2(capsys):
    code, out = _call(capsys, "submit", "not json")
    assert code == cli.EXIT_USAGE and out == "" and _rows() == []


def test_a_live_task_exits_2_and_writes_nothing(submit_env, tmp_path, capsys):
    assert _call(capsys, "submit", _arg(tmp_path, "1"))[0] == cli.EXIT_OK
    code, out = _call(capsys, "submit", _arg(tmp_path, "2", "1"))
    assert code == cli.EXIT_USAGE and out == ""
    assert _rows() == [("1", JobState.PENDING)]  # no row of task 2, no dead letter


def test_a_task_named_twice_in_one_array_writes_nothing(submit_env, tmp_path, capsys):
    code, _ = _call(capsys, "submit", _arg(tmp_path, "5", "5"))
    assert code == cli.EXIT_USAGE and _rows() == []


def test_a_submitted_entry_is_stored_and_reported(submit_env, tmp_path, capsys):
    brief = tmp_path / "b.md"
    brief.write_text("do it")
    jobs = json.dumps(
        [
            {"repo": "qoc", "task": "1", "brief": str(brief)},
            {
                "repo": "qoc",
                "task": "2",
                "brief": str(brief),
                "entry": "/odoo-dev:upgrade-implement",
            },
        ]
    )
    code, out, _ = _submit_err(capsys, jobs)
    assert code == cli.EXIT_OK
    assert [j["entry"] for j in json.loads(out)["jobs"]] == [
        "",
        "/odoo-dev:upgrade-implement",
    ]


@pytest.mark.parametrize("entry", ["/odoo-dev:pr", 7, None, ["x"]])
def test_an_unknown_entry_exits_2_and_writes_nothing(
    submit_env, tmp_path, capsys, entry
):
    brief = tmp_path / "b.md"
    brief.write_text("do it")
    jobs = json.dumps(
        [{"repo": "qoc", "task": "1", "brief": str(brief), "entry": entry}]
    )
    code, out, err = _submit_err(capsys, jobs)
    assert code == cli.EXIT_USAGE and out == "" and _rows() == []
    assert "entry must be one of" in err


def test_a_repo_with_no_checkout_exits_2_and_writes_nothing(
    submit_env, tmp_path, capsys
):
    code, out, err = _submit_err(capsys, _arg(tmp_path, "1", repo="a"))
    assert code == cli.EXIT_USAGE and out == "" and _rows() == []
    assert "repo a:" in err and str(submit_env.workspace("a")) in err


def test_a_repo_with_no_config_exits_2_and_writes_nothing(submit_env, tmp_path, capsys):
    git = submit_env.workspace("zzz") / ".git"
    git.mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    code, out, err = _submit_err(capsys, _arg(tmp_path, "1", repo="zzz"))
    assert code == cli.EXIT_USAGE and out == "" and _rows() == []
    assert "repo zzz:" in err and "github.com/*/zzz/.devcontainer" in err


def test_one_bad_repo_in_a_batch_writes_nothing_and_is_named_once(
    submit_env, tmp_path, capsys
):
    brief = tmp_path / "b.md"
    brief.write_text("x")
    jobs = [
        {"repo": repo, "task": task, "brief": str(brief)}
        for repo, task in (("qoc", "1"), ("a", "2"), ("a", "3"))
    ]
    code, _, err = _submit_err(capsys, json.dumps(jobs))
    assert code == cli.EXIT_USAGE and _rows() == []
    assert err.count("repo a:") == 1


def test_submit_without_settings_exits_2_and_writes_nothing(
    tmp_path, capsys, monkeypatch
):
    for name in ("DISPATCH_REPOS_DIR", "REPOS_DIR", "DISPATCH_CONFIG_ROOT"):
        monkeypatch.delenv(name, raising=False)
    code, out = _call(capsys, "submit", _arg(tmp_path, "1"))
    assert code == cli.EXIT_USAGE and out == "" and _rows() == []


def test_a_running_engine_picks_up_a_row_submitted_after_it_started(
    world, submit_env, tmp_path, capsys, monkeypatch  # noqa: F811
):
    engine, shell, _ = world
    sessions = iter(["sess-a", "sess-b"])
    monkeypatch.setattr(
        shell,
        "spawn",
        lambda argv, env: FakeProc(stream(next(sessions), structured=GOOD)),
    )
    _call(capsys, "submit", _arg(tmp_path, "10"))
    late = []
    settle = engine.gate.settle

    def settle_then_submit():
        settle()
        if not late:
            late.append(_call(capsys, "submit", _arg(tmp_path, "11")))

    monkeypatch.setattr(engine.gate, "settle", settle_then_submit)
    engine.run()
    assert late[0][0] == cli.EXIT_OK
    assert sorted(_rows()) == [("10", JobState.DONE), ("11", JobState.DONE)]


def test_report_matches_the_batch_while_locked_and_after(submit_env, tmp_path, capsys):
    submitted = json.loads(_call(capsys, "submit", _arg(tmp_path, "1", "2"))[1])
    batch = submitted["batch_id"]
    with DispatcherLock(lock_path()):
        code, out = _call(capsys, "report", batch)
    assert code == cli.EXIT_OK and json.loads(out) == submitted
    store = JobStore()
    first = submitted["jobs"][0]["job_id"]
    store.mark_running(first)
    store.complete(first, outcome="COMPLETED")
    store.close()
    after = json.loads(_call(capsys, "report", batch)[1])
    assert after["jobs"][0]["state"] == "DONE" and len(after["jobs"]) == 2
    assert after["jobs"][0].keys() == submitted["jobs"][0].keys()


def test_report_adds_the_bundle_when_its_directory_exists(submit_env, tmp_path, capsys):
    submitted = json.loads(_call(capsys, "submit", _arg(tmp_path, "1", "2"))[1])
    directory = ensure_bundle_dir(submitted["jobs"][0]["job_id"])
    jobs = json.loads(_call(capsys, "report", submitted["batch_id"])[1])["jobs"]
    assert jobs[0]["bundle"] == str(directory) and "bundle" not in jobs[1]


def test_report_of_an_unknown_batch_is_empty_and_exits_0(capsys):
    code, out = _call(capsys, "report", "nope")
    assert code == cli.EXIT_OK and json.loads(out) == {"batch_id": "nope", "jobs": []}


def test_the_exec_argv_carries_the_job_id(settings):
    argv = lifecycle.render_exec(settings, "qoc", "1", "job-7", "b")
    i = argv.index(f"{lifecycle.JOB_ID_VARIABLE}=job-7")
    assert lifecycle.JOB_ID_VARIABLE == "ODOO_JOB_ID" and argv[i - 1] == "--remote-env"
    assert i < argv.index("--")
