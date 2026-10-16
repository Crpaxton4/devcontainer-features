"""The admission loop, one job's life, and the entry point."""

import json
import os
import signal
import threading

import pytest
from odoo_sdk.state.dispatch_state import artifacts_root, bundle_dir
from odoo_sdk.state.jobs import DeadReason, JobState, JobStore
from odoo_sdk.state.runs_store import RunStore

from dispatcher import cli, queue
from dispatcher.engine import MEMORY_READ_ATTEMPTS, Engine, JobRun
from dispatcher.gates import MemoryReadError
from dispatcher.gates import AdmissionGate
from dispatcher.lifecycle import Completed
from dispatcher.lock import DispatcherLock
from support import GOOD, FakeProc, FakeShell, stream


@pytest.fixture
def world(settings, tmp_path):
    (settings.workspace("qoc") / ".git").mkdir(parents=True)
    (settings.workspace("qoc") / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    shell = FakeShell()
    mem = tmp_path / "meminfo"
    mem.write_text("MemAvailable: 9999999 kB\n")
    shell.reply(
        ["devcontainer", "up"],
        Completed(0, json.dumps({"outcome": "success", "containerId": "c"})),
    )
    mounts = [
        {
            "Type": "bind",
            "Source": str(settings.workspace("qoc")),
            "Destination": "/mnt/extra-addons",
        },
        {"Type": "bind", "Source": str(artifacts_root()), "Destination": "/artifacts"},
    ]
    shell.reply(["docker", "inspect"], Completed(0, json.dumps(mounts)))
    jobs, runs = JobStore(), RunStore()
    gate = AdmissionGate(1, 0, meminfo=mem)
    engine = Engine(settings, jobs, runs, shell, gate, log=lambda m: None)
    return engine, shell, mem


def _submit(engine, *pairs):
    return queue.submit(
        engine.jobs, [queue.JobSpec(r, t, "brief " + t) for r, t in pairs], "b"
    )


def test_a_good_run_completes_and_is_recorded(world):
    engine, shell, _ = world
    shell.proc = FakeProc(stream(structured=GOOD))
    (job,) = _submit(engine, ("qoc", "10"))
    engine.run()
    done = engine.jobs.get(job.job_id)
    assert done.state is JobState.DONE and done.outcome == "COMPLETED"
    assert done.session_id == "sess-1" and not engine.errors
    run = engine.runs.get_run("sess-1")
    assert (
        run.job_id == job.job_id
        and run.task_id == "10"
        and run.ended_us >= run.started_us
    )
    assert engine.runs.get_artifacts(job.job_id).has_envelope
    argv, env = shell.spawned[0]
    assert argv[0:2] == ["devcontainer", "exec"] and "COMPOSE_PROJECT_NAME" not in env
    assert env["ODOO_ACTIVE_REPOS"] == "qoc"
    assert bundle_dir(job.job_id).is_dir()
    assert shell.commands("docker", "compose", "-p", "qoc", "stop")  # idle: stopped


def test_collection_runs_before_the_container_may_stop(world, monkeypatch):
    engine, shell, _ = world
    shell.proc = FakeProc(stream(structured=GOOD))
    order = []
    import dispatcher.engine as eng

    real = eng.record_then_collect
    monkeypatch.setattr(
        eng,
        "record_then_collect",
        lambda *a, **k: order.append("collect") or real(*a, **k),
    )
    real_run = shell.run
    shell.run = lambda argv, **k: order.append(
        argv[1] if argv[0] == "docker" else argv[0]
    ) or real_run(argv, **k)
    _submit(engine, ("qoc", "10"))
    engine.run()
    assert order.index("collect") < order.index("compose")


def test_a_dead_letter_still_records_the_run_and_the_bundle(world):
    engine, shell, _ = world
    shell.proc = FakeProc(stream(is_error=True, subtype="success"), returncode=1)
    (job,) = _submit(engine, ("qoc", "11"))
    engine.run()
    dead = engine.jobs.get(job.job_id)
    assert dead.state is JobState.DEAD_LETTER
    assert dead.dead_reason == DeadReason.STRUCTURED_OUTPUT_MISSING.value
    assert "is_error=true" in dead.detail and "worktree" in dead.detail
    assert engine.runs.get_run("sess-1").outcome.value == "FAILED"
    assert bundle_dir(job.job_id).is_dir()


def test_session_launch_failure_has_a_bundle_and_no_run(world):
    engine, shell, _ = world
    shell.proc = FakeProc([], returncode=127, stderr="claude: not found")
    (job,) = _submit(engine, ("qoc", "12"))
    engine.run()
    dead = engine.jobs.get(job.job_id)
    assert dead.dead_reason == DeadReason.SESSION_LAUNCH_FAILED.value
    assert "claude: not found" in dead.detail
    assert engine.runs.iter_runs() == []
    assert engine.runs.get_artifacts(job.job_id) is not None


def test_spawn_oserror_is_a_launch_failure(world):
    engine, shell, _ = world
    shell.spawn_error = FileNotFoundError("devcontainer")
    (job,) = _submit(engine, ("qoc", "13"))
    engine.run()
    assert (
        engine.jobs.get(job.job_id).dead_reason
        == DeadReason.SESSION_LAUNCH_FAILED.value
    )


def test_container_start_failure(world):
    engine, shell, _ = world
    shell.replies[0] = (
        ["devcontainer", "up"],
        Completed(1, "", "compose file missing"),
    )
    (job,) = _submit(engine, ("qoc", "14"))
    engine.run()
    dead = engine.jobs.get(job.job_id)
    assert dead.dead_reason == DeadReason.CONTAINER_START_FAILED.value
    assert "compose file missing" in dead.detail
    assert not shell.spawned and engine.runs.get_artifacts(job.job_id) is not None


def test_workspace_mount_invalid(world):
    engine, shell, _ = world
    shell.replies[1] = (["docker", "inspect"], Completed(0, "[]"))
    (job,) = _submit(engine, ("qoc", "15"))
    engine.run()
    assert (
        engine.jobs.get(job.job_id).dead_reason
        == DeadReason.WORKSPACE_MOUNT_INVALID.value
    )
    assert not shell.spawned


def test_unexpected_admit_error_dead_letters_then_raises(world):
    engine, shell, _ = world
    shell.replies[0] = (["devcontainer", "up"], Completed(0, "x"))
    shell.run = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("kaboom"))
    (job,) = _submit(engine, ("qoc", "16"))
    with pytest.raises(RuntimeError):
        engine.run()
    assert (
        engine.jobs.get(job.job_id).dead_reason
        == DeadReason.CONTAINER_START_FAILED.value
    )


def test_killed_by_signal(world):
    engine, shell, _ = world
    shell.proc = FakeProc(stream(), returncode=-9)
    (job,) = _submit(engine, ("qoc", "17"))
    engine.run()
    assert engine.jobs.get(job.job_id).dead_reason == DeadReason.KILLED_BY_SIGNAL.value


def test_deadline_guard_kills_a_hung_job(world):
    engine, shell, _ = world
    engine.settings = engine.settings.__class__(
        **{**engine.settings.__dict__, "deadline_s": 0.05}
    )
    shell.proc = FakeProc(stream()[:1], hang=True)
    (job,) = _submit(engine, ("qoc", "18"))
    engine.run()
    assert shell.proc.killed and signal.SIGKILL in shell.groups
    dead = engine.jobs.get(job.job_id)
    assert dead.dead_reason == DeadReason.KILLED_BY_DEADLINE.value
    assert shell.commands("docker", "exec", "c", "pkill")  # the container side, too
    assert "container-side" in dead.detail


def test_deadline_fires_while_a_grandchild_holds_the_pipe(world, monkeypatch):
    """EOF never arrives, yet the guard must still record KILLED_BY_DEADLINE and free the slot."""
    import time

    import dispatcher.engine as eng

    monkeypatch.setattr(eng, "READER_GRACE_S", 0.2)
    monkeypatch.setattr(eng, "DRAIN_GRACE_S", 0.2)
    engine, shell, _ = world
    engine.settings = engine.settings.__class__(
        **{**engine.settings.__dict__, "deadline_s": 0.05}
    )
    shell.proc = FakeProc(stream()[:1], hang=True, pipe_held=True)
    (job,) = _submit(engine, ("qoc", "100"))
    start = time.monotonic()
    engine.run()
    # FakeProc.wait times out after 5s, so a missing killpg cannot hide here.
    assert time.monotonic() - start < 3
    assert not [
        t
        for t in threading.enumerate()
        if t.name.startswith(("reader-", "drain-")) and not t.daemon
    ]
    dead = engine.jobs.get(job.job_id)
    assert dead.dead_reason == DeadReason.KILLED_BY_DEADLINE.value
    assert signal.SIGKILL in shell.groups  # the process group, not just the CLI
    assert shell.commands("docker", "exec", "c", "pkill")
    assert "container-side" in dead.detail
    assert (
        engine.jobs.count_live("qoc") == 0 and engine.jobs.running_task_ids() == set()
    )
    assert shell.commands("docker", "compose", "-p", "qoc", "stop")
    shell.proc.stdout.never.set()
    shell.proc.stderr.never.set()


def test_terminate_signals_the_process_group(world):
    engine, shell, _ = world
    (job,) = _submit(engine, ("qoc", "101"))
    run = JobRun(engine, job)
    run.proc = shell.proc = FakeProc(hang=True)
    run.terminate()
    assert shell.groups == [signal.SIGTERM] and shell.proc.terminated


def test_request_stop_does_not_deadlock_when_the_lock_is_held(world):
    engine, _, _ = world
    with engine._lock:  # a signal handler arriving inside _start
        engine.request_stop()
    assert engine._stop.is_set()


def test_exit_nonzero_and_unreadable(world):
    engine, shell, _ = world
    shell.proc = FakeProc(["some text"], returncode=3)
    (a,) = _submit(engine, ("qoc", "19"))
    engine.run()
    assert (
        engine.jobs.get(a.job_id).dead_reason == DeadReason.SESSION_EXIT_NONZERO.value
    )
    shell.proc = FakeProc(['{"type": "result", oops'], returncode=0)
    (b,) = _submit(engine, ("qoc", "20"))
    engine.run()
    assert (
        engine.jobs.get(b.job_id).dead_reason
        == DeadReason.STRUCTURED_OUTPUT_UNREADABLE.value
    )


def test_a_record_failure_is_surfaced_after_the_rest_of_the_finally(world, monkeypatch):
    engine, shell, _ = world
    shell.proc = FakeProc(stream(structured=GOOD))
    monkeypatch.setattr(
        engine.runs, "record_run", lambda **k: (_ for _ in ()).throw(RuntimeError("db"))
    )
    (job,) = _submit(engine, ("qoc", "21"))
    engine.run()
    assert len(engine.errors) == 1
    assert engine.jobs.get(job.job_id).state is JobState.DONE
    assert engine.runs.get_artifacts(job.job_id) is not None
    assert shell.commands("docker", "compose", "-p", "qoc", "stop")


def test_duplicate_task_never_runs(world):
    engine, shell, _ = world
    shell.proc = FakeProc(stream(structured=GOOD))
    a, b = _submit(engine, ("qoc", "30"), ("qoc", "30"))
    assert b.dead_reason == DeadReason.DUPLICATE_TASK_IN_FLIGHT.value
    engine.run()
    assert len(shell.spawned) == 1


def test_siblings_share_one_container_which_stops_only_when_all_done(world):
    engine, shell, _ = world
    procs = [
        FakeProc(stream("s1", structured=GOOD)),
        FakeProc(stream("s2", structured=GOOD)),
    ]
    shell.spawn = lambda argv, env: shell.spawned.append((argv, env)) or procs.pop(0)
    _submit(engine, ("qoc", "40"), ("qoc", "41"))
    engine.run()
    assert len(shell.commands("devcontainer", "up")) == 2  # idempotent up per job
    stops = shell.commands("docker", "compose", "-p", "qoc", "stop")
    assert len(stops) >= 1
    # the first job's ending saw a pending/running sibling, so only the last may stop
    assert engine.jobs.count_live("qoc") == 0


def test_gate_blocks_admission_until_memory_returns(world):
    engine, shell, mem = world
    _write_atomic(mem, "MemAvailable: 10 kB\n")
    shell.proc = FakeProc(stream(structured=GOOD))
    (job,) = _submit(engine, ("qoc", "50"))
    logs = []
    engine.log = logs.append
    t = threading.Thread(target=engine.run)
    t.start()
    for _ in range(200):
        if logs:
            break
        threading.Event().wait(0.01)
    assert engine.jobs.get(job.job_id).state is JobState.PENDING and not shell.spawned
    _write_atomic(mem, "MemAvailable: 9999999 kB\n")
    t.join(10)
    assert engine.jobs.get(job.job_id).state is JobState.DONE
    assert "waiting" in logs[0]


def _write_atomic(path, text):
    """Write-then-rename, so a concurrent reader never sees a partial file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def test_a_partial_memory_read_is_retried_not_abandoned(world):
    engine, shell, mem = world
    mem.write_text("MemTot")  # deliberately partial: no MemAvailable line
    shell.proc = FakeProc(stream(structured=GOOD))
    (job,) = _submit(engine, ("qoc", "140"))
    logs = []
    engine.log = logs.append
    t = threading.Thread(target=engine.run)
    t.start()
    for _ in range(300):
        if any("memory read failed" in m for m in logs):
            break
        threading.Event().wait(0.01)
    assert engine.jobs.get(job.job_id).state is JobState.PENDING and not shell.spawned
    _write_atomic(mem, "MemAvailable: 9999999 kB\n")
    t.join(10)
    assert engine.jobs.get(job.job_id).state is JobState.DONE and not engine.errors


def test_a_persistently_unreadable_memory_figure_is_fatal_and_loud(
    world, capsys, monkeypatch
):
    engine, shell, mem = world
    mem.write_text("garbage")
    (job,) = _submit(engine, ("qoc", "141"))
    with pytest.raises(MemoryReadError):
        engine.run()
    assert engine.jobs.get(job.job_id).state is JobState.PENDING and not shell.spawned
    assert engine._memory_failures == MEMORY_READ_ATTEMPTS
    # through the entry point: non-zero, named on stderr, the job still PENDING
    assert cli._run_engine(engine) is True
    assert "remain PENDING" in capsys.readouterr().err


def test_a_good_read_resets_the_failure_count(world):
    engine, _, mem = world
    mem.write_text("garbage")
    assert engine._room() is False and engine._memory_failures == 1
    _write_atomic(mem, "MemAvailable: 9999999 kB\n")
    assert engine._room() is True and engine._memory_failures == 0


def test_admission_is_serial_and_settles(world):
    engine, shell, _ = world
    slept = []
    engine.gate = AdmissionGate(1, 3, meminfo=engine.gate._meminfo, sleep=slept.append)
    procs = [
        FakeProc(stream("s1", structured=GOOD)),
        FakeProc(stream("s2", structured=GOOD)),
    ]
    shell.spawn = lambda argv, env: shell.spawned.append((argv, env)) or procs.pop(0)
    _submit(engine, ("qoc", "60"), ("qoc", "61"))
    engine.run()
    assert slept == [3, 3]  # one settle per admitted job


def test_request_stop_terminates_running_sessions(world):
    engine, shell, _ = world
    shell.proc = FakeProc(stream()[:1], hang=True)
    (job,) = _submit(
        engine,
        ("qoc", "70"),
    )
    t = threading.Thread(target=engine.run)
    t.start()
    for _ in range(300):
        if shell.spawned and engine._active:
            break
        threading.Event().wait(0.01)
    engine.request_stop()
    t.join(10)
    assert shell.proc.terminated
    assert engine.jobs.get(job.job_id).dead_reason == DeadReason.KILLED_BY_SIGNAL.value


def test_idle_check_failure_never_fails_a_job(world):
    engine, shell, _ = world
    shell.proc = FakeProc(stream(structured=GOOD))
    original = shell.run

    def run(argv, **kw):
        if argv[:2] == ["docker", "compose"]:
            raise OSError("docker gone")
        return original(argv, **kw)

    shell.run = run
    (job,) = _submit(engine, ("qoc", "80"))
    engine.run()
    assert engine.jobs.get(job.job_id).state is JobState.DONE


def test_worktree_is_left_alone_when_reap_cannot_prove_a_push(world):
    engine, shell, _ = world
    shell.proc = FakeProc(stream(structured=GOOD))
    (job,) = _submit(engine, ("qoc", "90"))
    d = bundle_dir(job.job_id)
    d.mkdir(parents=True)
    (d / "task.json").write_text(
        json.dumps(
            {"build": {"branch": "feat", "worktree": "/mnt/extra-addons/.worktrees/x"}}
        )
    )
    engine.run()
    assert "does not exist" in engine.notes[job.job_id]["reap"]


def test_reap_exception_is_contained(world, monkeypatch):
    engine, shell, _ = world
    import dispatcher.engine as eng

    monkeypatch.setattr(
        eng, "reap", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    )
    shell.proc = FakeProc(stream(structured=GOOD))
    (job,) = _submit(engine, ("qoc", "91"))
    engine.run()
    assert "reap failed" in engine.notes[job.job_id]["reap"]


# -- cli ----------------------------------------------------------------


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("DISPATCH_REPOS_DIR", str(tmp_path / "repos"))
    monkeypatch.setenv("DISPATCH_CONFIG_ROOT", str(tmp_path / "c"))
    monkeypatch.setenv("DISPATCH_REPO_OWNER", "acme")


def test_cli_refuses_when_another_dispatcher_holds_the_lock(state_root, capsys):
    with DispatcherLock(state_root / "dispatcher.lock"):
        assert cli.main(["run"]) == cli.EXIT_LOCKED
    assert "another dispatcher" in capsys.readouterr().err


def test_cli_takes_the_lock_before_validating_anything(state_root, capsys):
    with DispatcherLock(state_root / "dispatcher.lock"):
        assert cli.main(["run", "not json"]) == cli.EXIT_LOCKED


def test_cli_usage_errors(monkeypatch, tmp_path, capsys):
    assert cli.main(["run", "not json"]) == cli.EXIT_USAGE
    brief = tmp_path / "b.md"
    brief.write_text("x")
    arg = json.dumps([{"repo": "qoc", "task": "1", "brief": str(brief)}])
    for name in (
        "DISPATCH_REPOS_DIR",
        "REPOS_DIR",
        "DISPATCH_CONFIG_ROOT",
        "DISPATCH_REPO_OWNER",
    ):
        monkeypatch.delenv(name, raising=False)
    assert cli.main(["run", arg]) == cli.EXIT_USAGE
    assert JobStore().list_by_state(JobState.PENDING) == []  # nothing stored


def test_cli_run_prints_the_report(monkeypatch, tmp_path, capsys, settings):
    _env(monkeypatch, tmp_path)
    mem = tmp_path / "mem"
    mem.write_text("MemAvailable: 9999999 kB\n")
    shell = FakeShell()
    shell.proc = FakeProc(stream(structured=GOOD))
    shell.reply(
        ["devcontainer", "up"],
        Completed(0, json.dumps({"outcome": "success", "containerId": "c"})),
    )
    ws = tmp_path / "repos" / "qoc"
    (ws / ".git").mkdir(parents=True)
    (ws / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    shell.reply(
        ["docker", "inspect"],
        Completed(
            0,
            json.dumps(
                [
                    {
                        "Type": "bind",
                        "Source": str(ws),
                        "Destination": "/mnt/extra-addons",
                    },
                    {
                        "Type": "bind",
                        "Source": str(artifacts_root()),
                        "Destination": "/artifacts",
                    },
                ]
            ),
        ),
    )
    monkeypatch.setattr(cli, "Shell", lambda: shell)
    monkeypatch.setattr(
        cli, "AdmissionGate", lambda gb, s: AdmissionGate(gb, 0, meminfo=mem)
    )
    monkeypatch.setattr(cli.signal, "signal", lambda *a: None)
    brief = tmp_path / "b.md"
    brief.write_text("do it")
    arg = json.dumps([{"repo": "qoc", "task": "1", "brief": str(brief)}])
    assert cli.main(["run", arg]) == cli.EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert (
        report["jobs"][0]["state"] == "DONE"
        and report["jobs"][0]["session_id"] == "sess-1"
    )
    assert cli.main(["dead-letters"]) == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out) == []
    # resume with nothing pending exits cleanly and reports nothing
    assert cli.main(["run"]) == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["jobs"] == []


def test_cli_reconciles_orphans_at_startup(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    store = JobStore()
    (job,) = queue.submit(store, [queue.JobSpec("qoc", "1", "x")], "b")
    store.mark_running(job.job_id)
    monkeypatch.setattr(cli, "Shell", FakeShell)
    monkeypatch.setattr(cli.signal, "signal", lambda *a: None)
    assert cli.main(["run"]) == cli.EXIT_OK
    capsys.readouterr()
    assert cli.main(["dead-letters"]) == cli.EXIT_OK
    dead = json.loads(capsys.readouterr().out)
    assert dead[0]["dead_reason"] == "ORPHANED_BY_RESTART"


def test_missing_or_empty_workspace_is_a_mount_failure(world, tmp_path):
    import shutil

    engine, shell, _ = world
    shutil.rmtree(engine.settings.workspace("qoc") / ".git")  # empty directory
    (a,) = _submit(engine, ("qoc", "110"))
    engine.run()
    shutil.rmtree(engine.settings.workspace("qoc"))  # missing altogether
    (b,) = _submit(engine, ("qoc", "111"))
    engine.run()
    for job in (a, b):
        dead = engine.jobs.get(job.job_id)
        assert dead.dead_reason == DeadReason.WORKSPACE_MOUNT_INVALID.value
        assert "not a git checkout" in dead.detail
    assert not shell.commands("devcontainer", "up") and not shell.spawned


def test_a_stop_during_admission_is_honoured(world):
    engine, shell, _ = world
    shell.proc = FakeProc(stream(structured=GOOD), hang=True)  # would run forever
    original = shell.run

    def run(argv, **kw):
        if argv[:2] == ["devcontainer", "up"]:
            engine.request_stop()  # Ctrl-C during a cold up
        return original(argv, **kw)

    shell.run = run
    (job,) = _submit(engine, ("qoc", "120"))
    engine.run()
    assert not shell.spawned
    dead = engine.jobs.get(job.job_id)
    assert dead.dead_reason == DeadReason.KILLED_BY_SIGNAL.value
    assert "during admission" in dead.detail
    assert engine.jobs.count_live("qoc") == 0


def test_a_stop_while_spawning_terminates_the_new_session(world):
    engine, shell, _ = world
    shell.proc = FakeProc(stream()[:1], hang=True)
    real_spawn = shell.spawn
    shell.spawn = lambda argv, env: (engine.request_stop(), real_spawn(argv, env=env))[
        1
    ]
    (job,) = _submit(engine, ("qoc", "121"))
    engine.run()
    assert shell.proc.terminated
    assert engine.jobs.get(job.job_id).dead_reason == DeadReason.KILLED_BY_SIGNAL.value


def test_the_timer_firing_after_a_clean_exit_is_not_a_deadline(world, monkeypatch):
    """The host process exited 0 but a grandchild holds the pipe; the timer fires in the grace."""
    import dispatcher.engine as eng

    monkeypatch.setattr(eng, "READER_GRACE_S", 0.4)
    monkeypatch.setattr(eng, "DRAIN_GRACE_S", 0.2)
    engine, shell, _ = world
    engine.settings = engine.settings.__class__(
        **{**engine.settings.__dict__, "deadline_s": 0.05}
    )
    shell.proc = FakeProc(stream()[:1], returncode=0, pipe_held=True)
    (job,) = _submit(engine, ("qoc", "130"))
    engine.run()
    dead = engine.jobs.get(job.job_id)
    assert dead.dead_reason != DeadReason.KILLED_BY_DEADLINE.value
    assert dead.dead_reason == DeadReason.STRUCTURED_OUTPUT_MISSING.value
    assert not shell.commands("docker", "exec", "c", "pkill") and shell.groups == []
    shell.proc.stdout.never.set()
    shell.proc.stderr.never.set()


def test_cli_rejects_a_nul_in_the_brief_path_with_exit_two():
    arg = json.dumps([{"repo": "qoc", "task": "1", "brief": "a\0b"}])
    assert cli.main(["run", arg]) == cli.EXIT_USAGE


def _cli_world(monkeypatch, tmp_path, meminfo_text):
    _env(monkeypatch, tmp_path)
    monkeypatch.setenv("DISPATCH_POLL_S", "0.01")
    mem = tmp_path / "mem"
    mem.write_text(meminfo_text)
    monkeypatch.setattr(cli, "Shell", FakeShell)
    monkeypatch.setattr(
        cli, "AdmissionGate", lambda gb, s: AdmissionGate(gb, 0, meminfo=mem)
    )
    handlers = {}
    monkeypatch.setattr(cli.signal, "signal", lambda n, h: handlers.update({n: h}))
    return handlers


def test_resume_with_a_fatal_memory_error_still_reports_the_pending_jobs(
    monkeypatch, tmp_path, capsys
):
    handlers = _cli_world(monkeypatch, tmp_path, "garbage")
    store = JobStore()
    (job,) = queue.submit(store, [queue.JobSpec("qoc", "1", "x")], "b")
    assert cli.main(["run"]) == cli.EXIT_JOB_ERROR
    out = capsys.readouterr()
    assert "remain PENDING" in out.err
    report = json.loads(out.out)
    assert [(j["job_id"], j["state"]) for j in report["jobs"]] == [
        (job.job_id, "PENDING")
    ]


def test_exit_zero_never_leaves_a_job_pending(monkeypatch, tmp_path, capsys):
    handlers = _cli_world(monkeypatch, tmp_path, "MemAvailable: 10 kB\n")
    brief = tmp_path / "b.md"
    brief.write_text("do it")
    arg = json.dumps([{"repo": "qoc", "task": "1", "brief": str(brief)}])
    timer = threading.Timer(0.3, lambda: handlers[signal.SIGTERM]())
    timer.start()
    assert cli.main(["run", arg]) == cli.EXIT_JOB_ERROR
    timer.join()
    report = json.loads(capsys.readouterr().out)
    assert [j["state"] for j in report["jobs"]] == ["PENDING"]
