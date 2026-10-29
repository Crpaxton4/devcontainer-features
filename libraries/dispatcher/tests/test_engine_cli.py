"""The admission loop, one job's life, and the entry point."""

import dataclasses
import json
import os
import signal
import threading
from pathlib import Path

import pytest
from odoo_dev_data.db import now_us
from odoo_dev_data.home import CONTAINER_HOME, job_bundle_dir
from odoo_dev_data.jobs import DeadReason, JobState, JobStore
from odoo_dev_data.locks import exclusive

from dispatcher import cli, lifecycle, queue
from dispatcher.engine import MEMORY_READ_ATTEMPTS, Engine, JobRun
from dispatcher.gates import MemoryReadError
from dispatcher.gates import AdmissionGate
from dispatcher.lifecycle import Completed
from .support import GOOD, FakeProc, FakeShell, stream, write_task_record


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
        {
            "Type": "bind",
            "Source": str(settings.home),
            "Destination": CONTAINER_HOME,
        },
    ]
    shell.reply(["docker", "inspect"], Completed(0, json.dumps(mounts)))
    jobs = JobStore(home=settings.home)
    gate = AdmissionGate(1, 0, meminfo=mem)
    engine = Engine(settings, jobs, shell, gate, log=lambda m: None)
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
    assert done.ended_us >= done.started_us
    assert done.envelope_json()["structured_output"]["outcome"] == "done"
    assert done.tool_version == "9.9.9" and done.question_count == 0
    assert done.has_envelope and done.has_session is False
    assert done.bundle_dir == str(
        job_bundle_dir("10", job.job_id, engine.settings.home)
    )
    argv, env = shell.spawned[0]
    assert argv[0:2] == ["devcontainer", "exec"] and "COMPOSE_PROJECT_NAME" not in env
    assert "ODOO_ACTIVE_REPOS" not in env
    assert Path(done.bundle_dir).is_dir()
    assert shell.commands("docker", "compose", "-p", "qoc", "stop")  # idle: stopped


def test_the_job_row_window_starts_at_the_spawn_and_ends_at_the_session_end(world):
    engine, shell, _ = world
    shell.proc = FakeProc(stream(structured=GOOD))
    base = now_us()
    clock = [base]
    engine.clock = lambda: clock[0]
    up_s, session_s = 1200, 30

    def advance(seconds):
        clock[0] += seconds * 1_000_000

    real_run, real_wait = shell.run, shell.proc.wait
    shell.run = lambda argv, **k: (
        advance(up_s) if argv[:2] == ["devcontainer", "up"] else None
    ) or real_run(argv, **k)
    shell.proc.wait = lambda: advance(session_s) or real_wait()
    (job,) = _submit(engine, ("qoc", "10"))
    engine.run()
    row = engine.jobs.get(job.job_id)
    assert row.started_us == base + up_s * 1_000_000  # the spawn clock, not admission
    assert row.ended_us - row.started_us == session_s * 1_000_000  # no cold start
    assert row.ended_us == base + (up_s + session_s) * 1_000_000


def test_a_run_row_without_a_spawn_falls_back_to_the_admit_time(world, monkeypatch):
    engine, shell, _ = world
    shell.replies[0] = (
        ["devcontainer", "up"],
        Completed(1, "", "compose file missing"),
    )
    seen = {}
    import dispatcher.engine as eng

    real = eng.record_then_collect
    monkeypatch.setattr(
        eng, "record_then_collect", lambda *a, **k: seen.update(k) or real(*a, **k)
    )
    (job,) = _submit(engine, ("qoc", "14"))
    engine.run()
    admitted = engine.jobs.get(job.job_id).started_us
    assert not shell.spawned and admitted > 0
    assert seen["started_us"] == admitted and seen["ended_us"] >= admitted
    assert engine.jobs.get(job.job_id).session_id == ""


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


def test_a_dead_letter_still_indexes_the_bundle(world):
    engine, shell, _ = world
    shell.proc = FakeProc(stream(is_error=True, subtype="success"), returncode=1)
    (job,) = _submit(engine, ("qoc", "11"))
    engine.run()
    dead = engine.jobs.get(job.job_id)
    assert dead.state is JobState.DEAD_LETTER
    assert dead.dead_reason == DeadReason.STRUCTURED_OUTPUT_MISSING.value
    assert "is_error=true" in dead.detail and "worktree" in dead.detail
    assert dead.outcome == "" and dead.session_id == "sess-1"
    assert dead.has_envelope and Path(dead.bundle_dir).is_dir()


def test_session_launch_failure_has_a_bundle_and_no_session(world):
    engine, shell, _ = world
    shell.proc = FakeProc([], returncode=127, stderr="claude: not found")
    (job,) = _submit(engine, ("qoc", "12"))
    engine.run()
    dead = engine.jobs.get(job.job_id)
    assert dead.dead_reason == DeadReason.SESSION_LAUNCH_FAILED.value
    assert "claude: not found" in dead.detail
    assert dead.session_id == "" and dead.bundle_dir


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
    assert not shell.spawned and engine.jobs.get(job.job_id).bundle_dir != ""


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


def test_an_unresolvable_config_dead_letters_one_job_and_the_run_continues(world):
    engine, shell, _ = world
    (engine.settings.workspace("ghost") / ".git").mkdir(parents=True)
    (engine.settings.workspace("ghost") / ".git" / "HEAD").write_text("ref: x\n")
    (bad,) = _submit(engine, ("ghost", "16"))
    (good,) = _submit(engine, ("qoc", "17"))
    engine.run()
    dead = engine.jobs.get(bad.job_id)
    assert dead.dead_reason == DeadReason.CONTAINER_START_FAILED.value
    assert "ghost" in dead.detail and "no devcontainer config" in dead.detail
    assert engine.jobs.get(good.job_id).dead_reason != (
        DeadReason.CONTAINER_START_FAILED.value
    )


PG = ["docker", "exec", "c", "pg_isready"]
ODOO = ["docker", "exec", "c", "odoo", "--version"]


def test_admit_probes_readiness_after_the_mount_assertion(world):
    engine, shell, _ = world
    (job,) = _submit(engine, ("qoc", "18"))
    run = JobRun(engine, engine.jobs.get(job.job_id))
    assert run.admit() is True
    assert len(shell.commands(*PG)) == 1 and len(shell.commands(*ODOO)) == 1
    argvs = [c for c, _ in shell.calls]
    inspect = argvs.index(shell.commands("docker", "inspect")[0])
    assert inspect < argvs.index(PG) < argvs.index(ODOO)


TOKEN = ["docker", "exec", "c", "gh", "auth", "token", "--user"]


def test_admit_checks_the_account_token_in_the_container(world):
    engine, shell, _ = world
    (job,) = _submit(engine, ("qoc", "21"))
    run = JobRun(engine, engine.jobs.get(job.job_id))
    assert run.admit() is True
    assert len(shell.commands(*TOKEN, "work")) == 1


def test_an_unset_account_dead_letters_naming_the_setting(world, monkeypatch):
    engine, shell, _ = world
    monkeypatch.delenv("ODOO_GITHUB_ACCOUNT")
    (job,) = _submit(engine, ("qoc", "22"))
    engine.run()
    dead = engine.jobs.get(job.job_id)
    assert dead.dead_reason == DeadReason.CONTAINER_START_FAILED.value
    assert "[github] account" in dead.detail and "ODOO_GITHUB_ACCOUNT" in dead.detail
    assert not shell.spawned


def test_a_missing_token_dead_letters_naming_the_account(world):
    engine, shell, _ = world
    shell.reply(TOKEN, Completed(1, "", "no token found"))
    (job,) = _submit(engine, ("qoc", "23"))
    engine.run()
    dead = engine.jobs.get(job.job_id)
    assert dead.dead_reason == DeadReason.CONTAINER_START_FAILED.value
    assert "'work'" in dead.detail and "no token found" in dead.detail
    assert "ODOO_GITHUB_ACCOUNT" in dead.detail
    assert not shell.spawned


def test_a_slow_probe_is_retried_and_the_job_proceeds(world, monkeypatch):
    engine, shell, _ = world
    slept = []
    monkeypatch.setattr(lifecycle.time, "sleep", slept.append)
    shell.queue_reply(PG, Completed(2, "", "no response"), Completed(1))
    shell.proc = FakeProc(stream(structured=GOOD))
    (job,) = _submit(engine, ("qoc", "19"))
    engine.run()
    assert len(shell.commands(*PG)) == 3
    assert slept == [lifecycle.READY_POLL_S] * 2
    assert engine.jobs.get(job.job_id).state is JobState.DONE


def test_an_expired_wait_is_a_container_start_failure(world, monkeypatch):
    engine, shell, _ = world
    monkeypatch.setattr(lifecycle.time, "sleep", lambda s: None)
    monkeypatch.setattr(lifecycle, "READY_TIMEOUT_S", 0)
    shell.reply(PG, Completed(2, "", "accepting no connections"))
    shell.reply(ODOO, Completed(127, "", "odoo: not found"))
    (job,) = _submit(engine, ("qoc", "20"))
    engine.run()
    dead = engine.jobs.get(job.job_id)
    assert dead.dead_reason == DeadReason.CONTAINER_START_FAILED.value
    assert "pg_isready exit=2" in dead.detail
    assert "accepting no connections" in dead.detail
    assert "odoo --version exit=127" in dead.detail
    assert not shell.spawned and engine.jobs.get(job.job_id).bundle_dir != ""


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


def test_a_stop_kill_that_did_not_run_is_noted_in_the_detail(world):
    engine, shell, _ = world
    shell.reply(["docker", "exec", "c", "pkill"], Completed(1, "", "no such container"))
    shell.proc = FakeProc(stream()[:1], hang=True)
    (job,) = _submit(engine, ("qoc", "71"))
    t = threading.Thread(target=engine.run)
    t.start()
    for _ in range(300):
        if shell.spawned and engine._active:
            break
        threading.Event().wait(0.01)
    engine.request_stop()
    t.join(10)
    detail = engine.jobs.get(job.job_id).detail
    assert "[the container-side kill did not run (exit 1: no such container)" in detail


def test_a_stop_on_a_finished_host_process_kills_nothing_in_the_container(world):
    engine, shell, _ = world
    (job,) = _submit(engine, ("qoc", "72"))
    run = JobRun(engine, job)
    run.proc = shell.proc = FakeProc()  # already exited
    run.terminate()
    run._kill_after_stop()
    assert not run.stop_requested and not shell.commands("docker", "exec")


def test_a_stop_after_the_deadline_kill_keeps_the_first_note(world):
    engine, shell, _ = world
    (job,) = _submit(engine, ("qoc", "73"))
    run = JobRun(engine, job)
    run.container_id = "c"
    run.stop_requested = True
    run.container_kill_note = "first"
    run._kill_after_stop()
    assert run.container_kill_note == "first" and not shell.commands("docker", "exec")


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
        engine.jobs,
        "record_result",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db")),
    )
    (job,) = _submit(engine, ("qoc", "21"))
    engine.run()
    assert len(engine.errors) == 1
    assert engine.jobs.get(job.job_id).state is JobState.RUNNING  # left for reconcile
    assert engine.jobs.count_live("qoc") == 1  # so the container is not stopped


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


def _ticking_clock(step_us):
    """A clock that moves ``step_us`` forward on every call.

    It starts at the wall clock: ``mark_running`` stamps the job row with the
    real time, and the job row's ``ended_us`` (taken from this clock since the
    run row's clock moved to the spawn) must never fall below it.
    """
    now = [now_us()]

    def clock():
        now[0] += step_us
        return now[0]

    return clock


def test_the_memory_wait_is_bounded_and_leaves_the_job_pending(world):
    engine, shell, mem = world
    _write_atomic(mem, "MemAvailable: 2048 kB\n")
    engine.clock = _ticking_clock(1_000_000_000)  # 1000 s per call; limit is 1800 s
    logs = []
    engine.log = logs.append
    (job,) = _submit(engine, ("qoc", "51"))
    engine.run()
    assert engine.jobs.get(job.job_id).state is JobState.PENDING and not shell.spawned
    assert len(logs) == 2 and "waiting" in logs[0]
    assert "1800 s" in logs[1] and "1024 MB threshold" in logs[1]
    assert "last measured: 2 MB" in logs[1] and "remain PENDING" in logs[1]


def test_an_unreadable_figure_is_named_when_the_wait_ends(world):
    engine, _, mem = world
    mem.write_text("garbage")
    engine.clock = _ticking_clock(2_000_000_000)
    logs = []
    engine.log = logs.append
    _submit(engine, ("qoc", "52"))
    engine.run()
    assert "no readable figure" in logs[-1]


def test_a_running_job_resets_the_wait_clock(world):
    engine, _, mem = world
    _write_atomic(mem, "MemAvailable: 10 kB\n")
    now = [0]
    engine.clock = lambda: now[0]
    engine._last_admit_us = 0
    busy = threading.Event()
    worker = threading.Thread(target=busy.wait)
    worker.start()
    try:
        now[0] = 5000 * 1_000_000  # far past the 1800 s limit
        # a live job holds the memory, so this is not a stall
        assert engine._waited_too_long([worker]) is False
        assert engine._last_admit_us == now[0]
    finally:
        busy.set()
        worker.join()
    now[0] += 1799 * 1_000_000
    assert engine._waited_too_long([]) is False
    now[0] += 1_000_000
    assert engine._waited_too_long([]) is True


def test_an_admission_within_the_timeout_is_unaffected(world):
    engine, shell, _ = world
    engine.settings = dataclasses.replace(engine.settings, admit_timeout_s=0)
    engine.clock = _ticking_clock(1_000_000_000)
    shell.proc = FakeProc(stream(structured=GOOD))
    (job,) = _submit(engine, ("qoc", "53"))
    engine.run()
    assert engine.jobs.get(job.job_id).state is JobState.DONE and not engine.errors


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
    dead = engine.jobs.get(job.job_id)
    assert dead.dead_reason == DeadReason.KILLED_BY_SIGNAL.value
    assert len(shell.commands("docker", "exec", "c", "pkill")) == 1
    assert dead.detail.count("[a kill was sent to the container-side claude") == 1


def test_a_stop_kill_uses_the_configured_entry(world):
    """The stop path renders the pkill pattern with settings.entry, as the deadline does."""
    engine, shell, _ = world
    engine.settings = dataclasses.replace(engine.settings, entry="/x:y")
    shell.proc = FakeProc(stream()[:1], hang=True)
    _submit(engine, ("qoc", "71"))
    t = threading.Thread(target=engine.run)
    t.start()
    for _ in range(300):
        if shell.spawned and engine._active:
            break
        threading.Event().wait(0.01)
    engine.request_stop()
    t.join(10)
    (cmd,) = shell.commands("docker", "exec", "c", "pkill")
    assert cmd[-1] == lifecycle.kill_pattern("71", "/x:y") and "/x:y" in cmd[-1]


UPGRADE = "/odoo-dev:upgrade-implement"


def test_a_job_entry_reaches_the_prompt(world):
    engine, shell, _ = world
    shell.proc = FakeProc(stream(structured=GOOD))
    queue.submit(
        engine.jobs, [queue.JobSpec("qoc", "80", "the brief", entry=UPGRADE)], "b"
    )
    engine.run()
    argv, _env = shell.spawned[0]
    assert argv[argv.index("-p") + 1] == f"{UPGRADE} 80\n\nthe brief"


def test_a_job_entry_reaches_the_stop_kill_pattern(world):
    engine, shell, _ = world
    shell.proc = FakeProc(stream()[:1], hang=True)
    queue.submit(engine.jobs, [queue.JobSpec("qoc", "81", "b", entry=UPGRADE)], "b")
    t = threading.Thread(target=engine.run)
    t.start()
    for _ in range(300):
        if shell.spawned and engine._active:
            break
        threading.Event().wait(0.01)
    engine.request_stop()
    t.join(10)
    (cmd,) = shell.commands("docker", "exec", "c", "pkill")
    assert cmd[-1] == lifecycle.kill_pattern("81", UPGRADE)


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
    write_task_record(
        engine.settings.home,
        job.task_id,
        json.dumps(
            {"build": {"branch": "feat", "worktree": "/mnt/extra-addons/.worktrees/x"}}
        ),
    )
    engine.run()
    assert "does not exist" in engine.notes[job.job_id]["reap"]


def test_a_pushed_worktree_is_reaped_through_the_container_before_the_idle_check(
    world, settings
):
    engine, shell, _ = world
    shell.proc = FakeProc(stream(structured=GOOD))
    (job,) = _submit(engine, ("qoc", "92"))
    (settings.workspace("qoc") / ".worktrees/x").mkdir(parents=True)
    write_task_record(
        engine.settings.home,
        job.task_id,
        json.dumps(
            {"build": {"branch": "feat", "worktree": "/mnt/extra-addons/.worktrees/x"}}
        ),
    )
    sha = "c" * 40
    git = [*lifecycle.render_exec_prefix(settings, "qoc"), "--", "git"]
    wt = "/mnt/extra-addons/.worktrees/x"
    shell.reply([*git, "-C", wt, "status"], Completed(0, ""))
    shell.reply([*git, "-C", wt, "rev-parse"], Completed(0, sha + "\n"))
    shell.reply(
        [*git, "-C", "/mnt/extra-addons", "ls-remote"],
        Completed(0, f"{sha}\trefs/heads/feat\n"),
    )
    engine.run()
    assert engine.notes[job.job_id]["reaped"] is True
    argvs = [c for c, _ in shell.calls]
    removal = next(i for i, c in enumerate(argvs) if "remove" in c)
    assert argvs[removal][:2] == ["devcontainer", "exec"]
    idle = [i for i, c in enumerate(argvs) if c[:2] == ["docker", "compose"]]
    assert all(removal < i for i in idle)


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
    config = tmp_path / "c" / "github.com" / "any-owner" / "qoc" / ".devcontainer"
    config.mkdir(parents=True, exist_ok=True)
    (config / "devcontainer.json").write_text("{}", "utf-8")


def test_cli_refuses_when_another_dispatcher_holds_the_lock(home, capsys):
    with exclusive("dispatcher", home=home):
        assert cli.main(["run"]) == cli.EXIT_LOCKED
    assert "another dispatcher" in capsys.readouterr().err


def test_cli_takes_the_lock_before_validating_anything(home, capsys):
    with exclusive("dispatcher", home=home):
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
    ):
        monkeypatch.delenv(name, raising=False)
    assert cli.main(["run", arg]) == cli.EXIT_USAGE
    assert JobStore().list_by_state(JobState.PENDING) == []  # nothing stored


def test_cli_missing_invariants_file_is_a_usage_error(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    gone = tmp_path / "gone.md"
    monkeypatch.setenv("DISPATCH_INVARIANTS_FILE", str(gone))
    brief = tmp_path / "b.md"
    brief.write_text("x")
    arg = json.dumps([{"repo": "qoc", "task": "1", "brief": str(brief)}])
    assert cli.main(["run", arg]) == cli.EXIT_USAGE
    assert str(gone) in capsys.readouterr().err
    assert JobStore().list_by_state(JobState.PENDING) == []  # not a dead letter


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
                        "Source": str(settings.home),
                        "Destination": CONTAINER_HOME,
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
    dead = engine.jobs.get(job.job_id)
    assert dead.dead_reason == DeadReason.KILLED_BY_SIGNAL.value
    assert shell.commands("docker", "exec", "c", "pkill")
    assert "container-side" in dead.detail


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


def test_the_bounded_memory_wait_exits_one_and_names_the_threshold(
    monkeypatch, tmp_path, capsys
):
    _cli_world(monkeypatch, tmp_path, "MemAvailable: 3072 kB\n")
    monkeypatch.setenv("DISPATCH_ADMIT_TIMEOUT_S", "0")
    monkeypatch.setenv("MIN_FREE_GB", "1")
    store = JobStore()
    (job,) = queue.submit(store, [queue.JobSpec("qoc", "1", "x")], "b")
    assert cli.main(["run"]) == cli.EXIT_JOB_ERROR
    out = capsys.readouterr()
    assert "1024 MB threshold" in out.err and "last measured: 3 MB" in out.err
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
