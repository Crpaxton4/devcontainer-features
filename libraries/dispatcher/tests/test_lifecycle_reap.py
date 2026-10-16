"""Command-line rendering, container lifecycle, mount assertion, reap, collection."""

import json
import os
import subprocess

import pytest
from odoo_sdk.state.dispatch_state import artifacts_root, bundle_dir
from odoo_sdk.state.jobs import JobStore
from odoo_sdk.state.runs_store import RunOutcome, RunStore

from dispatcher import collect, lifecycle, queue
from dispatcher.lifecycle import Completed, Shell
from dispatcher.reap import host_worktree, reap
from support import GOOD, FakeShell, stream


def test_exec_command_line_is_section_eight_exactly(settings):
    argv = lifecycle.render_exec(settings, "qoc", "1234", "job-1", "the brief")
    cfg = str(settings.config("qoc"))
    ws = str(settings.workspace("qoc"))
    assert argv[:11] == [
        "devcontainer",
        "exec",
        "--config",
        cfg,
        "--workspace-folder",
        ws,
        "--remote-env",
        "ODOO_DEV_STATE_DIR=/artifacts/job-1",
        "--remote-env",
        "ODOO_UNATTENDED=1",  # the contract with plugins/odoo-dev/hooks/unattended-guard.sh
        "--",
    ]
    assert argv[11:14] == ["claude", "-p", "1234\n\nthe brief"]
    rest = argv[14:]
    assert rest[:4] == [
        "--agent",
        settings.agent,
        "--append-system-prompt-file",
        settings.invariants_file,
    ]
    assert (
        "--output-format" in rest
        and rest[rest.index("--output-format") + 1] == "stream-json"
    )
    assert "--verbose" in rest
    assert json.loads(rest[rest.index("--json-schema") + 1])["required"][0] == "summary"
    assert rest[rest.index("--permission-mode") + 1] == "dontAsk"
    denied = rest[rest.index("--disallowedTools") + 1].split()
    assert denied == ["Bash(docker:*)", "Edit(~/.claude/**)", "Write(~/.claude/**)"]
    assert "--override-config" not in argv
    assert "--max-turns" not in argv and "--max-budget-usd" not in argv
    assert not any("COMPOSE_PROJECT_NAME" in a for a in argv)


def test_up_command_line_mounts_the_bundle_root(settings):
    argv = lifecycle.render_up(settings, "qoc")
    assert argv[:2] == ["devcontainer", "up"]
    assert "--override-config" not in argv
    mount = argv[argv.index("--mount") + 1]
    assert mount == f"type=bind,source={artifacts_root()},target=/artifacts"
    assert argv[argv.index("--workspace-folder") + 1] == str(settings.workspace("qoc"))
    assert "--additional-features" not in argv
    from dataclasses import replace

    argv = lifecycle.render_up(replace(settings, additional_features="{}"), "qoc")
    assert argv[argv.index("--additional-features") + 1] == "{}"


def test_child_env_never_carries_compose_project_name():
    env = lifecycle.child_env(["a", "b"], {"COMPOSE_PROJECT_NAME": "x", "HOME": "/h"})
    assert "COMPOSE_PROJECT_NAME" not in env
    assert env["ODOO_ACTIVE_REPOS"] == "a,b" and env["HOME"] == "/h"
    assert "COMPOSE_PROJECT_NAME" not in lifecycle.child_env([])


def _up_ok(cid="cid1"):
    return Completed(
        0, "noise\n" + json.dumps({"outcome": "success", "containerId": cid})
    )


def test_up_returns_container_id(settings):
    shell = FakeShell()
    shell.reply(["devcontainer", "up"], _up_ok("abc"))
    assert lifecycle.up(shell, settings, "qoc", {}) == "abc"


@pytest.mark.parametrize(
    "reply",
    [
        Completed(1, "", "boom"),
        Completed(0, "no json here"),
        Completed(0, json.dumps({"outcome": "error", "message": "x"})),
        Completed(0, "{broken"),
    ],
)
def test_up_failures(settings, reply):
    shell = FakeShell()
    shell.reply(["devcontainer", "up"], reply)
    with pytest.raises(lifecycle.ContainerStartError):
        lifecycle.up(shell, settings, "qoc", {})


def _mounts(settings, artifacts=None, workspace=None):
    return json.dumps(
        [
            {
                "Type": "bind",
                "Source": str(workspace or settings.workspace("qoc")),
                "Destination": "/mnt/extra-addons",
            },
            {
                "Type": "bind",
                "Source": str(artifacts or artifacts_root()),
                "Destination": "/artifacts",
            },
        ]
    )


def test_mount_assertion_passes(settings):
    shell = FakeShell()
    shell.reply(["docker", "inspect"], Completed(0, _mounts(settings)))
    lifecycle.assert_mounts(shell, settings, "qoc", "cid", {})


@pytest.mark.parametrize(
    "stdout,needle",
    [
        ("[]", "--workspace-folder"),
        ("null", "--workspace-folder"),
        ("not json", "not JSON"),
    ],
)
def test_mount_assertion_catches_missing_binds(settings, stdout, needle):
    shell = FakeShell()
    shell.reply(["docker", "inspect"], Completed(0, stdout))
    with pytest.raises(lifecycle.MountInvalidError, match=needle):
        lifecycle.assert_mounts(shell, settings, "qoc", "cid", {})


def test_mount_assertion_catches_wrong_source_and_missing_artifacts(settings, tmp_path):
    shell = FakeShell()
    shell.reply(
        ["docker", "inspect"],
        Completed(
            0,
            _mounts(
                settings, artifacts=tmp_path / "other", workspace=tmp_path / "wrong"
            ),
        ),
    )
    with pytest.raises(lifecycle.MountInvalidError) as err:
        lifecycle.assert_mounts(shell, settings, "qoc", "cid", {})
    assert "workspace-folder" in err.value.detail and "/artifacts" in err.value.detail


def test_mount_assertion_inspect_failure(settings):
    shell = FakeShell()
    shell.reply(["docker", "inspect"], Completed(1, "", "no such container"))
    with pytest.raises(lifecycle.MountInvalidError):
        lifecycle.assert_mounts(shell, settings, "qoc", "cid", {})


def test_the_dispatcher_never_creates_the_mount(settings):
    shell = FakeShell()
    shell.reply(["docker", "inspect"], Completed(0, "[]"))
    with pytest.raises(lifecycle.MountInvalidError):
        lifecycle.assert_mounts(shell, settings, "qoc", "cid", {})
    assert not settings.workspace("qoc").exists()


def test_idle_check_needs_running_and_pending_both_empty():
    store = JobStore()
    shell = FakeShell()
    pending, running = queue.submit(
        store, [queue.JobSpec("Qoc", "1", "x"), queue.JobSpec("Qoc", "2", "x")], "b"
    )
    store.mark_running(running.job_id)
    assert lifecycle.stop_if_idle(shell, store, "Qoc", {}) is False  # pending + running
    store.dead_letter(pending.job_id, "SESSION_EXIT_NONZERO")
    assert lifecycle.stop_if_idle(shell, store, "Qoc", {}) is False  # running only
    store.complete(running.job_id, outcome="COMPLETED")
    assert lifecycle.stop_if_idle(shell, store, "Qoc", {}) is True
    assert shell.commands("docker", "compose", "-p", "qoc", "stop")
    assert not [c for c, _ in shell.calls if "rm" in c or "down" in c]


def test_stop_falls_back_to_the_container_and_reports_failure():
    shell = FakeShell()
    shell.reply(["docker", "compose"], Completed(1))
    assert lifecycle.stop_project(shell, "Qoc", {}) is True
    assert shell.commands("docker", "stop", "qoc-odoo-1")
    shell.reply(["docker", "stop"], Completed(1))
    shell.replies.reverse()
    assert lifecycle.stop_project(shell, "Qoc", {}) is False


def test_transcript_path(settings):
    assert lifecycle.transcript_path(settings, "") is None
    assert lifecycle.transcript_path(settings, "s1") is None
    path = settings.claude_home / "projects" / "p" / "s1.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    assert lifecycle.transcript_path(settings, "s1") == path


def test_shell_wraps_subprocess(tmp_path):
    shell = Shell()
    assert shell.run(["true"], env={}).returncode == 0
    assert shell.run(["/no/such/binary"], env={}).returncode == 127
    slow = shell.run(["sleep", "5"], env=dict(os.environ), timeout=0.05)
    assert slow.returncode == 124
    proc = shell.spawn(["echo", "hi"], env=dict(os.environ))
    assert proc.stdout.read() == "hi\n"
    proc.wait()


# -- reap ---------------------------------------------------------------


def _git(*args, cwd):
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )


@pytest.fixture
def repo(tmp_path):
    remote = tmp_path / "remote.git"
    work = tmp_path / "repos" / "qoc"
    work.mkdir(parents=True)
    _git("init", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    _git("init", "-b", "main", cwd=work)
    _git("remote", "add", "origin", str(remote), cwd=work)
    (work / "f").write_text("1")
    _git("add", "f", cwd=work)
    _git("commit", "-m", "init", cwd=work)
    _git("push", "origin", "main", cwd=work)
    _git("worktree", "add", "-b", "feat", ".worktrees/t1", cwd=work)
    return work


def test_host_worktree_translation(tmp_path):
    assert (
        host_worktree("/mnt/extra-addons/.worktrees/x", tmp_path)
        == tmp_path / ".worktrees/x"
    )
    assert host_worktree("/elsewhere/x", tmp_path) is None


def test_reap_leaves_an_unpushed_branch_alone(repo):
    result = reap(Shell(), repo, "/mnt/extra-addons/.worktrees/t1", "feat")
    assert not result.reaped and "does not exist on the remote" in result.note
    assert (repo / ".worktrees/t1").is_dir()


def test_reap_removes_a_pushed_matching_worktree(repo):
    _git("push", "origin", "feat", cwd=repo / ".worktrees/t1")
    result = reap(Shell(), repo, "/mnt/extra-addons/.worktrees/t1", "feat")
    assert result.reaped
    assert not (repo / ".worktrees/t1").exists()


def test_reap_refuses_dirty_and_diverged_worktrees(repo):
    wt = repo / ".worktrees/t1"
    _git("push", "origin", "feat", cwd=wt)
    (wt / "new").write_text("x")
    assert (
        "uncommitted"
        in reap(Shell(), repo, "/mnt/extra-addons/.worktrees/t1", "feat").note
    )
    _git("add", "new", cwd=wt)
    _git("commit", "-m", "more", cwd=wt)
    note = reap(Shell(), repo, "/mnt/extra-addons/.worktrees/t1", "feat").note
    assert "does not match" in note and wt.is_dir()


@pytest.mark.parametrize(
    "path,branch,needle",
    [
        ("", "b", "nothing reaped"),
        ("/mnt/extra-addons/x", "", "nothing reaped"),
        ("/etc/passwd", "b", "not a worktree"),
        ("/mnt/extra-addons/../escape", "b", "not a worktree"),
        ("/mnt/extra-addons/", "b", "not a worktree"),
        ("/mnt/extra-addons/missing", "b", "does not exist"),
    ],
)
def test_reap_guards(repo, path, branch, needle):
    result = reap(Shell(), repo, path, branch)
    assert not result.reaped and needle in result.note


def test_reap_reports_git_failures(repo):
    shell = FakeShell()
    wt = "/mnt/extra-addons/.worktrees/t1"
    for prefix, reason in [
        (["git", "-C", str(repo / ".worktrees/t1"), "status"], "git status failed"),
        (
            ["git", "-C", str(repo / ".worktrees/t1"), "rev-parse"],
            "git rev-parse failed",
        ),
        (["git", "-C", str(repo), "ls-remote"], "git ls-remote failed"),
    ]:
        shell = FakeShell()
        shell.reply(prefix, Completed(1, "", "bad"))
        shell.reply(
            ["git", "-C", str(repo / ".worktrees/t1"), "status"], Completed(0, "")
        )
        shell.reply(["git"], Completed(0, "abc\n"))
        shell.replies.sort(key=lambda r: -len(r[0]))
        assert reason in reap(shell, repo, wt, "feat").note
    shell = FakeShell()
    shell.reply(["git", "-C", str(repo), "worktree"], Completed(1, "", "locked"))
    shell.reply(["git", "-C", str(repo / ".worktrees/t1"), "status"], Completed(0, ""))
    shell.reply(["git"], Completed(0, "abc\n"))
    shell.replies.sort(key=lambda r: -len(r[0]))
    shell.replies.insert(
        0,
        (["git", "-C", str(repo), "ls-remote"], Completed(0, "abc\trefs/heads/feat\n")),
    )
    assert "locked" in reap(shell, repo, wt, "feat").note


# -- collection and recording -------------------------------------------


def _parse(lines):
    parser = collect.StreamParser()
    seen = [parser.feed(line) for line in lines]
    return parser, seen


def test_stream_parser_reads_session_envelope_and_version():
    parser, seen = _parse(stream(structured=GOOD) + ["", "  "])
    assert seen[0] == "sess-1" and seen[1] is None
    assert parser.session_id == "sess-1" and parser.tool_version == "9.9.9"
    assert parser.envelope["structured_output"]["summary"] == "did the thing"
    assert parser.envelope_text.startswith("{")


def test_stream_parser_flags_unreadable_result_and_ignores_noise():
    parser, _ = _parse(["garbage", "[1]", '{"type": "result", broken'])
    assert parser.saw_output and parser.unreadable and parser.envelope is None


def test_task_record_helpers(state_root):
    d = bundle_dir("j1")
    d.mkdir(parents=True)
    assert collect.read_task_record("j1") == {}
    (d / "task.json").write_text("{bad")
    assert collect.read_task_record("j1") == {}
    (d / "task.json").write_text("[1]")
    assert collect.read_task_record("j1") == {}
    record = {"build": {"branch": "b", "worktree": "/w"}, "pr": {"number": 7}}
    (d / "task.json").write_text(json.dumps(record))
    got = collect.read_task_record("j1")
    assert (
        collect.branch_of(got),
        collect.worktree_of(got),
        collect.pr_number_of(got),
    ) == ("b", "/w", 7)
    assert collect.pr_number_of({"pr": 3}) == 3
    assert collect.pr_number_of({"pr": {"number": True}}) == 0
    assert collect.pr_number_of({}) == 0
    assert collect.branch_of({"build": []}) == "" and collect.worktree_of({}) == ""


def _job():
    store = JobStore()
    (job,) = queue.submit(store, [queue.JobSpec("qoc", "1", "brief")], "b")
    return store.mark_running(job.job_id)


def test_run_row_is_written_before_artifacts_are_collected(monkeypatch, tmp_path):
    order = []
    runs = RunStore()
    real_record = runs.record_run
    monkeypatch.setattr(
        runs, "record_run", lambda **kw: order.append("run") or real_record(**kw)
    )
    real_collect = collect.collect_artifacts
    monkeypatch.setattr(
        collect,
        "collect_artifacts",
        lambda *a, **kw: order.append("collect") or real_collect(*a, **kw),
    )
    parser, _ = _parse(stream(structured=GOOD))
    job = _job()
    write = collect.record_then_collect(
        runs,
        parser=parser,
        job=job,
        ended_us=job.started_us + 5,
        failed=False,
        transcript=None,
        record={"build": {"branch": "feat"}},
    )
    assert order == ["run", "collect"]
    assert write.recorded and write.outcome is RunOutcome.COMPLETED
    run = runs.get_run("sess-1")
    assert (run.branch, run.tool_version, run.brief, run.task_id) == (
        "feat",
        "9.9.9",
        "brief",
        "1",
    )
    assert runs.get_envelope("sess-1")["structured_output"]["outcome"] == "done"
    assert "session.jsonl" in "\n".join(
        write.harvest.missing
    )  # absence is recorded only


def test_absence_changes_nothing_else_and_failed_run_is_failed():
    runs = RunStore()
    parser, _ = _parse(stream())  # envelope with no structured value
    job = _job()
    write = collect.record_then_collect(
        runs,
        parser=parser,
        job=job,
        ended_us=job.started_us,
        failed=True,
        transcript=None,
        record={},
    )
    assert (
        write.outcome is RunOutcome.FAILED
        and runs.get_run("sess-1").outcome is RunOutcome.FAILED
    )
    assert runs.get_artifacts(job.job_id) is not None


def test_no_session_means_no_run_row_but_still_a_bundle():
    runs = RunStore()
    job = _job()
    write = collect.record_then_collect(
        runs,
        parser=collect.StreamParser(),
        job=job,
        ended_us=job.started_us,
        failed=True,
        transcript=None,
        record={},
    )
    assert not write.recorded and runs.iter_runs() == []
    assert (bundle_dir(job.job_id) / "harvest.json").is_file()


@pytest.mark.parametrize(
    "reported,questions,expected",
    [
        ("done", [], RunOutcome.COMPLETED),
        (
            "done",
            [{"question": "q", "assumption": "a", "why": "w"}],
            RunOutcome.COMPLETED_WITH_QUESTIONS,
        ),
        ("partial", [], RunOutcome.COMPLETED_WITH_QUESTIONS),
        ("blocked", [], RunOutcome.BLOCKED),
    ],
)
def test_outcome_uses_the_sdk_mapping(reported, questions, expected):
    env = {"structured_output": {**GOOD, "outcome": reported, "questions": questions}}
    assert collect.stored_outcome(env, failed=False) is expected
    assert collect.stored_outcome(env, failed=True) is RunOutcome.FAILED
    assert collect.stored_outcome(None, failed=False) is RunOutcome.FAILED


def test_a_failing_run_write_does_not_stop_collection_and_is_surfaced(monkeypatch):
    runs = RunStore()

    def boom(**kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(runs, "record_run", boom)
    parser, _ = _parse(stream(structured=GOOD))
    job = _job()
    write = collect.record_then_collect(
        runs,
        parser=parser,
        job=job,
        ended_us=job.started_us,
        failed=False,
        transcript=None,
        record={},
    )
    assert isinstance(write.record_error, RuntimeError) and write.harvest is not None


def test_collection_failure_is_a_note_not_an_error(monkeypatch):
    def boom(*a, **kw):
        raise OSError("nope")

    monkeypatch.setattr(collect, "collect_artifacts", boom)
    parser, _ = _parse(stream(structured=GOOD))
    job = _job()
    write = collect.record_then_collect(
        RunStore(),
        parser=parser,
        job=job,
        ended_us=job.started_us,
        failed=False,
        transcript=None,
        record={},
    )
    assert write.recorded and "collection failed" in write.notes[0]


def test_kill_in_container_notes_what_is_known():
    shell = FakeShell()
    note = lifecycle.kill_in_container(shell, "cid", "12.3", {})
    cmd = shell.commands("docker", "exec", "cid", "pkill")[0]
    assert cmd[-1] == lifecycle.kill_pattern("12.3") and "not verified" in note
    shell.reply(["docker", "exec"], Completed(1, "", "no process"))
    assert "may still be alive" in lifecycle.kill_in_container(shell, "cid", "1", {})
    assert "may still be alive" in lifecycle.kill_in_container(shell, "", "1", {})


@pytest.mark.parametrize(
    "cmdline,matches",
    [
        ("claude -p 123\n\nbrief", True),
        ("/usr/local/bin/claude -p 123 more", True),
        ("node /usr/lib/node_modules/.bin/claude -p 123\n\nbrief", True),
        ("claude -p 1234\n\nbrief", False),
        ("claude -p 12\n\nbrief", False),
        ("claude -p 99\n\nthe brief says claude -p 123 and more", False),
        ("sh -c claude -p 123 x", False),
    ],
)
def test_kill_pattern_is_anchored(cmdline, matches):
    """Checked with grep -E, the same extended regex dialect pkill uses."""
    found = subprocess.run(
        ["grep", "-Ezc", "--", lifecycle.kill_pattern("123")],
        input=cmdline,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert (found == "1") is matches


def test_kill_pattern_cannot_be_widened_by_a_dotted_task():
    assert lifecycle.kill_pattern("1.2").count(r"1\.2") == 1


def test_mount_assertion_checks_the_workspace_destination(settings):
    shell = FakeShell()
    mounts = json.loads(_mounts(settings))
    mounts[0]["Destination"] = "/somewhere/else"
    shell.reply(["docker", "inspect"], Completed(0, json.dumps(mounts)))
    with pytest.raises(lifecycle.MountInvalidError, match="/mnt/extra-addons"):
        lifecycle.assert_mounts(shell, settings, "qoc", "cid", {})


def test_mount_assertion_rejects_a_parent_directory_bind(settings):
    shell = FakeShell()
    mounts = json.loads(_mounts(settings))
    mounts[0]["Source"] = str(settings.workspace("qoc").parent)
    shell.reply(["docker", "inspect"], Completed(0, json.dumps(mounts)))
    with pytest.raises(lifecycle.MountInvalidError):
        lifecycle.assert_mounts(shell, settings, "qoc", "cid", {})


def test_admission_calls_carry_timeouts(settings):
    shell = FakeShell()
    shell.reply(["devcontainer", "up"], _up_ok())
    shell.reply(["docker", "inspect"], Completed(0, _mounts(settings)))
    lifecycle.up(shell, settings, "qoc", {})
    lifecycle.assert_mounts(shell, settings, "qoc", "cid", {})
    lifecycle.stop_project(shell, "qoc", {})
    seen = {tuple(a[:2]): t for a, t in shell.timeouts}
    assert seen[("devcontainer", "up")] == 1200
    assert seen[("docker", "inspect")] == 60
    assert seen[("docker", "compose")] == 120


def test_up_timeout_is_a_start_failure(settings):
    shell = FakeShell()
    shell.reply(["devcontainer", "up"], Completed(124, "", "timed out after 1200s"))
    with pytest.raises(lifecycle.ContainerStartError, match="timed out"):
        lifecycle.up(shell, settings, "qoc", {})


def test_real_killpg_reaches_a_grandchild_holding_the_pipe():
    """sh runs a background sleep that inherits the pipe; killpg must free it."""
    import time

    shell = Shell()
    proc = shell.spawn(["sh", "-c", "sleep 8 & wait"], env=dict(os.environ))
    time.sleep(0.2)
    start = time.monotonic()
    shell.killpg(proc, 9)
    assert proc.stdout.read() == ""  # EOF arrives because the whole group died
    proc.wait()
    assert time.monotonic() - start < 4
    shell.killpg(proc, 9)  # already gone: no error


def _orphan(shell=None, session="s-orphan", worktree=None):
    store, runs = JobStore(), RunStore()
    (job,) = queue.submit(store, [queue.JobSpec("qoc", "1", "brief")], "b")
    store.mark_running(job.job_id)
    if session:
        store.set_session(job.job_id, session)
    if worktree:
        d = bundle_dir(job.job_id)
        d.mkdir(parents=True)
        (d / "task.json").write_text(json.dumps({"build": {"worktree": worktree}}))
    return store, runs, job


def test_reconcile_inspects_and_writes_a_failed_run_for_a_session(settings):
    store, runs, job = _orphan(worktree="/mnt/extra-addons/.worktrees/x")
    shell = FakeShell()
    shell.reply(["docker", "inspect"], Completed(0, "true\n"))
    assert queue.reconcile(store, runs, shell, settings) == [job.job_id]
    dead = store.get(job.job_id)
    assert dead.dead_reason == "ORPHANED_BY_RESTART"
    assert "container qoc-odoo-1 is running" in dead.detail
    assert "may still be alive" in dead.detail and "does not exist" in dead.detail
    assert "FAILED run row was written" in dead.detail
    assert runs.get_run("s-orphan").outcome is RunOutcome.FAILED
    assert runs.get_artifacts(job.job_id) is not None


def test_reconcile_says_what_it_could_not_see(settings):
    store, runs, job = _orphan(session="")
    shell = FakeShell()
    shell.reply(["docker", "inspect"], Completed(1, "", "gone"))
    queue.reconcile(store, runs, shell, settings)
    detail = store.get(job.job_id).detail
    assert "could not be inspected" in detail and "no worktree is recorded" in detail
    assert "no run row was written" in detail and runs.iter_runs() == []
    assert bundle_dir(job.job_id).is_dir()


def test_reconcile_without_tools_still_dead_letters():
    store, _, job = _orphan(session="")
    queue.reconcile(store)
    assert "were not inspected" in store.get(job.job_id).detail


def test_reconcile_survives_a_failing_run_write(settings, monkeypatch):
    store, runs, job = _orphan()
    monkeypatch.setattr(
        runs, "record_run", lambda **k: (_ for _ in ()).throw(RuntimeError("db"))
    )
    queue.reconcile(store, runs, FakeShell(), settings)
    assert "could not be written" in store.get(job.job_id).detail
    store2, runs2, job2 = _orphan(session="")
    monkeypatch.setattr(
        queue, "record_then_collect", lambda **k: (_ for _ in ()).throw(OSError("x"))
    )
    queue.reconcile(store2, runs2, FakeShell(), settings)
    assert "could not be recorded" in store2.get(job2.job_id).detail


def test_artifacts_bind_is_asserted_on_its_own(settings):
    shell = FakeShell()
    mounts = json.loads(_mounts(settings))[:1]  # workspace right, no /artifacts
    shell.reply(["docker", "inspect"], Completed(0, json.dumps(mounts)))
    with pytest.raises(lifecycle.MountInvalidError, match="/artifacts"):
        lifecycle.assert_mounts(shell, settings, "qoc", "cid", {})


def test_every_subprocess_has_a_timeout(settings):
    shell = FakeShell()
    lifecycle.kill_in_container(shell, "cid", "1", {})
    shell.reply(["docker", "compose"], Completed(1))
    lifecycle.stop_project(shell, "qoc", {})
    reap(shell, settings.workspace("qoc"), "/mnt/extra-addons/.w", "b")
    seen = {tuple(a[:2]): t for a, t in shell.timeouts}
    assert seen[("docker", "exec")] == 30
    assert seen[("docker", "stop")] == 120 and seen[("docker", "compose")] == 120


def test_git_calls_have_a_timeout(repo):
    shell = FakeShell()
    shell.reply(["git"], Completed(0, ""))
    shell.reply(
        ["git", "-C", str(repo), "ls-remote"], Completed(0, "abc\trefs/heads/f\n")
    )
    reap(shell, repo, "/mnt/extra-addons/.worktrees/t1", "feat")
    gits = [t for a, t in shell.timeouts if a[0] == "git"]
    assert gits and set(gits) == {120}


def test_reconcile_inspect_has_a_timeout(settings):
    store, runs, _ = _orphan(session="")
    shell = FakeShell()
    queue.reconcile(store, runs, shell, settings)
    assert [t for a, t in shell.timeouts if a[:2] == ["docker", "inspect"]] == [60]


def test_require_workspace(settings):
    with pytest.raises(lifecycle.MountInvalidError, match="not a git checkout"):
        lifecycle.require_workspace(settings, "qoc")
    (settings.workspace("qoc")).mkdir(parents=True)
    with pytest.raises(lifecycle.MountInvalidError):
        lifecycle.require_workspace(settings, "qoc")
    (settings.workspace("qoc") / ".git").write_text("gitdir: x")  # a worktree file
    lifecycle.require_workspace(settings, "qoc")
