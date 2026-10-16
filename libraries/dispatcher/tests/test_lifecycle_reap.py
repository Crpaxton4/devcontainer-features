"""Command-line rendering, container lifecycle, mount assertion, reap, collection."""

import json
import os
import subprocess

import pytest
from odoo_sdk.state.dispatch_state import bundle_dir, odoo_dev_dir
from odoo_sdk.state.jobs import JobStore
from odoo_sdk.state.runs_store import RunOutcome, RunStore

from dispatcher import collect, lifecycle, queue
from dispatcher.lifecycle import Completed, Shell
from dispatcher.reap import host_worktree, reap
from dispatcher.settings import DEFAULT_ENTRY
from support import GOOD, FakeShell, stream, write_task_record


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
        "ODOO_DEV_STATE_DIR=/usr/local/share/odoo-dev/dispatch/artifacts/job-1",
        "--remote-env",
        "ODOO_UNATTENDED=1",  # the contract with plugins/odoo-dev/hooks/unattended-guard.sh
        "--",
    ]
    assert argv[11:14] == [
        "claude",
        "-p",
        "/odoo-dev:implement 1234\n\nthe brief",  # the entry, then the task id
    ]
    rest = argv[14:]
    assert "--agent" not in argv  # the forked command names its own agent
    assert rest[:2] == [
        "--append-system-prompt",
        "Standing line.\nNever ask.\n",  # the file's text, not its path
    ]
    assert settings.invariants_file not in argv
    assert (
        "--output-format" in rest
        and rest[rest.index("--output-format") + 1] == "stream-json"
    )
    assert "--verbose" in rest
    assert json.loads(rest[rest.index("--json-schema") + 1])["required"][0] == "summary"
    assert rest[rest.index("--permission-mode") + 1] == "dontAsk"
    denied = [rest[i + 1] for i, arg in enumerate(rest) if arg == "--disallowedTools"]
    assert denied == [
        "Bash(docker:*)",
        "Edit(//usr/local/share/claude-home/**)",
        "Write(//usr/local/share/claude-home/**)",
        "mcp__odoo-mcp__start_task",
        "mcp__odoo-mcp__stop_task",
    ]
    assert not any(" " in rule for rule in denied)  # one rule per argument
    assert not any("~/.claude" in a for a in argv)
    assert "--override-config" not in argv
    assert "--max-turns" not in argv and "--max-budget-usd" not in argv
    assert not any("COMPOSE_PROJECT_NAME" in a for a in argv)


def test_up_command_line_passes_no_mount(settings):
    argv = lifecycle.render_up(settings, "qoc")
    assert argv[:2] == ["devcontainer", "up"]
    assert "--override-config" not in argv
    # A running container ignores --mount; the Feature's odoo-dev bind carries the bundle.
    assert "--mount" not in argv
    assert "--additional-features" in argv
    assert argv[argv.index("--workspace-folder") + 1] == str(settings.workspace("qoc"))
    features = argv[argv.index("--additional-features") + 1]
    assert features == (
        '{"ghcr.io/crpaxton4/devcontainer-features/personal-features":{}}'
    )
    from dataclasses import replace

    argv = lifecycle.render_up(replace(settings, additional_features="{}"), "qoc")
    assert argv[argv.index("--additional-features") + 1] == "{}"


def test_child_env_never_carries_compose_project_name():
    env = lifecycle.child_env({"COMPOSE_PROJECT_NAME": "x", "HOME": "/h"})
    assert "COMPOSE_PROJECT_NAME" not in env
    assert env == {"HOME": "/h"}
    assert "COMPOSE_PROJECT_NAME" not in lifecycle.child_env()


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


def _mounts(settings, odoo_dev=None, workspace=None):
    return json.dumps(
        [
            {
                "Type": "bind",
                "Source": str(workspace or settings.workspace("qoc")),
                "Destination": "/mnt/extra-addons",
            },
            {
                "Type": "bind",
                "Source": str(odoo_dev or odoo_dev_dir()),
                "Destination": "/usr/local/share/odoo-dev",
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


def test_mount_assertion_catches_wrong_source_and_wrong_odoo_dev(settings, tmp_path):
    shell = FakeShell()
    shell.reply(
        ["docker", "inspect"],
        Completed(
            0,
            _mounts(
                settings, odoo_dev=tmp_path / "other", workspace=tmp_path / "wrong"
            ),
        ),
    )
    with pytest.raises(lifecycle.MountInvalidError) as err:
        lifecycle.assert_mounts(shell, settings, "qoc", "cid", {})
    detail = err.value.detail
    assert "workspace-folder" in detail and "/usr/local/share/odoo-dev" in detail


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


WT = "/mnt/extra-addons/.worktrees/t1"
HEAD = "a" * 40


@pytest.fixture
def worktree(settings):
    """The worktree as the host sees it through the mount: it only has to exist."""
    (settings.workspace("qoc") / ".worktrees/t1").mkdir(parents=True)


def _exec(settings):
    """The argv every container-side command must start with."""
    return lifecycle.render_exec_prefix(settings, "qoc") + ["--"]


def _container_git(settings, shell, *, status="", head=HEAD, remote=None, rc=None):
    """Script the container-side git answers; ``rc`` fails the named command."""
    pre = _exec(settings) + ["git"]
    remote = f"{HEAD}\trefs/heads/feat\n" if remote is None else remote
    rc = rc or {}
    for name, prefix, out in [
        ("remove", ["-C", "/mnt/extra-addons", "worktree", "remove"], ""),
        ("ls-remote", ["-C", "/mnt/extra-addons", "ls-remote"], remote),
        ("rev-parse", ["-C", WT, "rev-parse"], head + "\n"),
        ("status", ["-C", WT, "status"], status),
    ]:
        if name in rc:
            shell.reply(pre + prefix, Completed(rc[name], "", "bad"))
        else:
            shell.reply(pre + prefix, Completed(0, out))


def test_host_worktree_translation(tmp_path):
    assert (
        host_worktree("/mnt/extra-addons/.worktrees/x", tmp_path)
        == tmp_path / ".worktrees/x"
    )
    assert host_worktree("/elsewhere/x", tmp_path) is None


def test_render_exec_prefix_names_the_project_container(settings):
    assert lifecycle.render_exec_prefix(settings, "qoc") == [
        "devcontainer",
        "exec",
        "--config",
        str(settings.config("qoc")),
        "--workspace-folder",
        str(settings.workspace("qoc")),
    ]
    assert lifecycle.render_exec(settings, "qoc", "1", "j", "b")[:6] == (
        lifecycle.render_exec_prefix(settings, "qoc")
    )


def test_reap_removes_a_pushed_matching_worktree_through_the_container(
    settings, worktree
):
    shell = FakeShell()
    _container_git(settings, shell)
    env = {"PATH": "/bin"}
    result = reap(shell, settings, "qoc", WT, "feat", env)
    assert result.reaped and WT in result.note
    assert [c[len(_exec(settings)) + 1 :] for c, _ in shell.calls] == [
        ["-C", WT, "status", "--porcelain"],
        ["-C", WT, "rev-parse", "HEAD"],
        [
            "-C",
            "/mnt/extra-addons",
            "ls-remote",
            "--heads",
            "origin",
            "refs/heads/feat",
        ],
        ["-C", "/mnt/extra-addons", "worktree", "remove", WT],
    ]
    assert all(c[: len(_exec(settings))] == _exec(settings) for c, _ in shell.calls)
    assert all(c[len(_exec(settings))] == "git" for c, _ in shell.calls)
    assert all(e == env for _, e in shell.calls)
    assert {t for _, t in shell.timeouts} == {120}


@pytest.mark.parametrize(
    "answers,needle",
    [
        ({"status": " M f\n"}, "uncommitted"),
        ({"remote": ""}, "does not exist on the remote"),
        ({"remote": f"{'b' * 40}\trefs/heads/feat\n"}, "does not match"),
    ],
)
def test_reap_leaves_an_unpushed_worktree_alone(settings, worktree, answers, needle):
    shell = FakeShell()
    _container_git(settings, shell, **answers)
    result = reap(shell, settings, "qoc", WT, "feat")
    assert not result.reaped and needle in result.note and WT in result.note
    assert not shell.commands(
        *_exec(settings), "git", "-C", "/mnt/extra-addons", "worktree"
    )


@pytest.mark.parametrize(
    "command,reason",
    [
        ("status", "git status failed: bad"),
        ("rev-parse", "git rev-parse failed: bad"),
        ("ls-remote", "git ls-remote failed: bad"),
        ("remove", "git worktree remove failed: bad"),
    ],
)
def test_reap_reports_a_failing_container_command_as_a_note(
    settings, worktree, command, reason
):
    shell = FakeShell()
    _container_git(settings, shell, rc={command: 1})
    result = reap(shell, settings, "qoc", WT, "feat")
    assert (
        not result.reaped and reason in result.note and "left in place" in result.note
    )


def test_reap_reports_a_devcontainer_exec_that_cannot_reach_the_container(
    settings, worktree
):
    shell = FakeShell()
    shell.reply(_exec(settings), Completed(1, "No container found", ""))
    result = reap(shell, settings, "qoc", WT, "feat")
    assert not result.reaped and "No container found" in result.note
    assert len(shell.calls) == 1  # it stops at the first failure


@pytest.mark.parametrize(
    "path,branch,needle",
    [
        ("", "b", "nothing reaped"),
        ("/mnt/extra-addons/x", "", "nothing reaped"),
        ("/etc/passwd", "b", "not a worktree"),
        ("/mnt/extra-addons/../escape", "b", "not a worktree"),
        ("/mnt/extra-addons/a/../../etc", "b", "not a worktree"),
        ("/mnt/extra-addons/", "b", "not a worktree"),
        ("/mnt/extra-addonsx/y", "b", "not a worktree"),
        ("/mnt/extra-addons/missing", "b", "does not exist; nothing to reap"),
    ],
)
def test_reap_guards(settings, worktree, path, branch, needle):
    shell = FakeShell()
    result = reap(shell, settings, "qoc", path, branch)
    assert not result.reaped and needle in result.note
    assert shell.calls == []  # no guard reaches the container


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
    bundle_dir("j1").mkdir(parents=True)
    assert collect.read_task_record("j1", "42") == {}
    write_task_record("j1", "42", "{bad")
    assert collect.read_task_record("j1", "42") == {}
    write_task_record("j1", "42", "[1]")
    assert collect.read_task_record("j1", "42") == {}
    record = {"build": {"branch": "b", "worktree": "/w"}, "pr": {"number": 7}}
    write_task_record("j1", "42", json.dumps(record))
    got = collect.read_task_record("j1", "42")
    assert (
        collect.branch_of(got),
        collect.worktree_of(got),
        collect.pr_number_of(got),
    ) == ("b", "/w", 7)
    assert collect.pr_number_of({"pr": 3}) == 3
    assert collect.pr_number_of({"pr": {"number": True}}) == 0
    assert collect.pr_number_of({}) == 0
    assert collect.branch_of({"build": []}) == "" and collect.worktree_of({}) == ""


def test_the_record_state_dir_sh_writes_is_the_record_the_collector_reads(state_root):
    record = {"build": {"branch": "b", "worktree": "/w"}}
    path = write_task_record("j1", "42", json.dumps(record))
    assert path == bundle_dir("j1") / "tasks" / "42" / "task.json"
    got = collect.read_task_record("j1", "42")
    assert (collect.branch_of(got), collect.worktree_of(got)) == ("b", "/w")
    assert collect.collect_artifacts("j1", "42").has_task_json
    assert collect.read_task_record("j1", "43") == {}  # another task's record


def test_a_top_level_task_json_is_not_the_agents_record(state_root):
    bundle_dir("j1").mkdir(parents=True)
    (bundle_dir("j1") / "task.json").write_text('{"build": {"branch": "old"}}')
    assert collect.read_task_record("j1", "42") == {}
    harvest = collect.collect_artifacts("j1", "42")
    assert not harvest.has_task_json
    assert any("the agent wrote no task.json" in line for line in harvest.missing)


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
        started_us=job.started_us + 3,
        ended_us=job.started_us + 5,
        failed=False,
        transcript=None,
        record={"build": {"branch": "feat"}},
    )
    assert order == ["run", "collect"]
    row = runs.get_run("sess-1")  # the run row's clock is the argument, not the job's
    assert (row.started_us, row.ended_us) == (job.started_us + 3, job.started_us + 5)
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
        started_us=job.started_us,
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
        started_us=job.started_us,
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
        started_us=job.started_us,
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
        started_us=job.started_us,
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
    lifecycle.kill_in_container(shell, "cid", "12.3", {}, "/x:run")  # a set entry
    assert shell.commands("docker", "exec", "cid", "pkill")[-1][-1] == (
        lifecycle.kill_pattern("12.3", "/x:run")
    )
    shell.reply(["docker", "exec"], Completed(1, "", "no process"))
    assert "may still be alive" in lifecycle.kill_in_container(shell, "cid", "1", {})
    assert "may still be alive" in lifecycle.kill_in_container(shell, "", "1", {})


@pytest.mark.parametrize(
    "cmdline,matches",
    [
        ("claude -p /odoo-dev:implement 123\n\nbrief", True),
        ("/usr/local/bin/claude -p /odoo-dev:implement 123 more", True),
        (
            "node /usr/lib/node_modules/.bin/claude -p /odoo-dev:implement 123\n\nb",
            True,
        ),
        (
            "/x/@anthropic-ai/claude-code/bin/claude.exe -p /odoo-dev:implement 123 b",
            True,
        ),
        ("claude.exe -p /odoo-dev:implement 123\n\nbrief", True),
        ("node /x/@anthropic-ai/claude-code/cli.js -p /odoo-dev:implement 123 b", True),
        ("cli.js -p /odoo-dev:implement 123\n\nbrief", True),
        ("claude -p /odoo-dev:implement 1234\n\nbrief", False),
        ("claude -p /odoo-dev:implement 12\n\nbrief", False),
        (
            "claude -p /odoo-dev:implement 99\n\nsays claude -p /odoo-dev:implement 123 x",
            False,
        ),
        ("sh -c claude -p /odoo-dev:implement 123 x", False),
        ("claude -p /odoo-dev:test 123\n\nbrief", False),  # another entry
        ("claude -p 123\n\nbrief --agent odoo-dev:odoo-dev-implementer", False),
        ("claude --agent odoo-dev:odoo-dev-implementer -p 123\n\nbrief", False),
        ("claude -p /odoo-dev:implementX 123\n\nbrief", False),
        ("not-claude.exe -p /odoo-dev:implement 123 x", False),
    ],
)
def test_kill_pattern_is_anchored(cmdline, matches):
    """Checked with grep -E, the same extended regex dialect pkill uses."""
    found = subprocess.run(
        ["grep", "-Ezc", "--", lifecycle.kill_pattern("123", "/odoo-dev:implement")],
        input=cmdline,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert (found == "1") is matches


def test_kill_pattern_defaults_to_the_default_entry():
    assert lifecycle.kill_pattern("5") == lifecycle.kill_pattern("5", DEFAULT_ENTRY)


def test_kill_pattern_cannot_be_widened_by_a_dotted_task_or_entry():
    assert lifecycle.kill_pattern("1.2").count(r"1\.2") == 1
    assert lifecycle.kill_pattern("1", "/a.b:c").count(r"/a\.b:c") == 1


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


class _Clock:
    """A fake clock whose sleep advances it, so a wait expires without waiting."""

    def __init__(self):
        self.now = 0.0
        self.slept = []

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def test_wait_ready_returns_when_both_probes_pass():
    shell, clock = FakeShell(), _Clock()
    lifecycle.wait_ready(shell, "cid", {}, sleep=clock.sleep, clock=lambda: clock.now)
    assert shell.commands("docker", "exec", "cid", "pg_isready")
    assert shell.commands("docker", "exec", "cid", "odoo", "--version")
    assert clock.slept == []
    assert {t for _, t in shell.timeouts} == {lifecycle.READY_PROBE_TIMEOUT_S}


def test_wait_ready_retries_until_both_pass():
    shell, clock = FakeShell(), _Clock()
    shell.queue_reply(["docker", "exec", "cid", "pg_isready"], Completed(2))
    shell.queue_reply(["docker", "exec", "cid", "odoo"], Completed(1), Completed(1))
    lifecycle.wait_ready(shell, "cid", {}, sleep=clock.sleep, clock=lambda: clock.now)
    assert len(shell.commands("docker", "exec", "cid", "odoo", "--version")) == 3
    assert clock.slept == [lifecycle.READY_POLL_S] * 2


def test_wait_ready_expiry_names_both_probes_and_the_last_output():
    shell, clock = FakeShell(), _Clock()
    shell.reply(["docker", "exec", "cid", "pg_isready"], Completed(2, "", "x" * 2000))
    shell.reply(["docker", "exec", "cid", "odoo"], Completed(124, "", "timed out"))
    with pytest.raises(lifecycle.ContainerStartError) as raised:
        lifecycle.wait_ready(
            shell,
            "cid",
            {},
            timeout_s=10,
            poll_s=4,
            sleep=clock.sleep,
            clock=lambda: clock.now,
        )
    detail = raised.value.detail
    assert detail.startswith("readiness: not ready after 10s: pg_isready exit=2: ")
    assert "x" * 1000 in detail and "x" * 1001 not in detail
    assert "odoo --version exit=124: timed out" in detail
    assert clock.slept == [4, 4, 4]  # attempts at t=0, 4, 8, 12: expired at 12


def test_wait_ready_defaults_are_bounded_under_the_up_timeout():
    assert lifecycle.READY_TIMEOUT_S < lifecycle.UP_TIMEOUT_S
    assert lifecycle.READY_PROBE_TIMEOUT_S < lifecycle.READY_TIMEOUT_S


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
        write_task_record(
            job.job_id, job.task_id, json.dumps({"build": {"worktree": worktree}})
        )
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
    assert "timed from the admit time" in dead.detail
    assert "no spawn time survives a restart" in dead.detail
    row = runs.get_run("s-orphan")
    assert row.outcome is RunOutcome.FAILED and row.started_us == dead.started_us
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


def test_odoo_dev_bind_is_asserted_on_its_own(settings):
    shell = FakeShell()
    mounts = json.loads(_mounts(settings))[:1]  # workspace right, no Feature bind
    shell.reply(["docker", "inspect"], Completed(0, json.dumps(mounts)))
    with pytest.raises(lifecycle.MountInvalidError, match="personal-features") as err:
        lifecycle.assert_mounts(shell, settings, "qoc", "cid", {})
    assert "stop it and let the dispatcher create it" in err.value.detail
    assert "/usr/local/share/odoo-dev" in err.value.detail


def test_bundle_root_outside_the_odoo_dev_bind_is_refused(
    settings, tmp_path, monkeypatch
):
    monkeypatch.setenv("DISPATCH_ARTIFACTS_DIR", str(tmp_path / "elsewhere"))
    shell = FakeShell()
    shell.reply(["docker", "inspect"], Completed(0, _mounts(settings)))
    with pytest.raises(lifecycle.MountInvalidError, match="cannot see it") as err:
        lifecycle.assert_mounts(shell, settings, "qoc", "cid", {})
    assert "personal-features" not in err.value.detail  # the bind itself is right


def test_bundle_root_under_the_odoo_dev_bind_passes(settings, monkeypatch):
    monkeypatch.setenv(
        "DISPATCH_ARTIFACTS_DIR", str(odoo_dev_dir() / "dispatch" / "other")
    )
    shell = FakeShell()
    shell.reply(["docker", "inspect"], Completed(0, _mounts(settings)))
    lifecycle.assert_mounts(shell, settings, "qoc", "cid", {})


def test_every_subprocess_has_a_timeout(settings):
    shell = FakeShell()
    lifecycle.kill_in_container(shell, "cid", "1", {})
    shell.reply(["docker", "compose"], Completed(1))
    lifecycle.stop_project(shell, "qoc", {})
    seen = {tuple(a[:2]): t for a, t in shell.timeouts}
    assert seen[("docker", "exec")] == 30
    assert seen[("docker", "stop")] == 120 and seen[("docker", "compose")] == 120


def test_container_git_calls_have_a_timeout(settings, worktree):
    shell = FakeShell()
    _container_git(settings, shell)
    reap(shell, settings, "qoc", WT, "feat")
    execs = [t for a, t in shell.timeouts if a[:2] == ["devcontainer", "exec"]]
    assert len(execs) == 4 and set(execs) == {120}


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
    dot_git = settings.workspace("qoc") / ".git"
    dot_git.write_text("")  # an empty .git file
    with pytest.raises(lifecycle.MountInvalidError):
        lifecycle.require_workspace(settings, "qoc")
    dot_git.write_text("junk")
    with pytest.raises(lifecycle.MountInvalidError):
        lifecycle.require_workspace(settings, "qoc")
    dot_git.write_text("gitdir: /elsewhere/.git/worktrees/x")  # a worktree file
    lifecycle.require_workspace(settings, "qoc")
    dot_git.unlink()
    dot_git.mkdir()  # an empty .git directory
    with pytest.raises(lifecycle.MountInvalidError):
        lifecycle.require_workspace(settings, "qoc")
    (dot_git / "HEAD").write_text("ref: refs/heads/main\n")
    lifecycle.require_workspace(settings, "qoc")
