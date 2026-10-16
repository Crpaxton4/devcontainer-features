"""The admission loop and one job's life (design sections 7, 8 and 10).

Admission is serial and happens on the calling thread: measure, admit, bring
the project's container up and assert its mounts, let the machine settle, and
only then measure again. The long part, the session, runs on a worker thread.

Everything after the exec runs from :meth:`JobRun.finish`, which is reached
from a ``finally`` on every path, because the expensive cases are the ones
that crash. The order inside it is fixed:

1. decide the cause (or success) from what the dispatcher saw,
2. write the run row, then collect artifacts (``collect.record_then_collect``),
3. reap the worktree if, and only if, it is provably pushed,
4. move the job to DONE or DEAD_LETTER,
5. only then the idle check, which may stop the container.
"""

import signal
import sys
import threading
from collections import deque
from collections.abc import Callable

from odoo_sdk.state.artifacts import ensure_bundle_dir
from odoo_sdk.state.dispatch_state import now_us, resolve_now_us
from odoo_sdk.state.jobs import (
    DeadReason,
    InvalidJobTransitionError,
    Job,
    JobStore,
)
from odoo_sdk.state.runs_store import RunStore

from . import lifecycle
from .collect import (
    RunWrite,
    StreamParser,
    branch_of,
    read_task_record,
    record_then_collect,
    worktree_of,
)
from .deadletter import Ending, classify, describe
from .gates import AdmissionGate, MemoryReadError
from .lifecycle import (
    ContainerStartError,
    MountInvalidError,
    Shell,
    StageError,
    child_env,
)
from .queue import live_repos, pending_jobs
from .reap import ReapResult, reap
from .settings import Settings

STDERR_TAIL_LINES = 40
#: A failed read of the memory figure is retried this many times in a row (one
#: poll interval apart) before it is treated as fatal. The gate itself still
#: raises on a bad read and never admits on a guess.
MEMORY_READ_ATTEMPTS = 5
#: How long to wait for the stderr drain once the host process has exited.
DRAIN_GRACE_S = 5
#: After the host process has exited, the stream is already in the pipe and is
#: read in moments. If it is not finished by then, something else still holds
#: the pipe (a grandchild) and the reader is abandoned instead of waited for.
READER_GRACE_S = 10
#: Upper bound on waiting for the deadline's container-side kill to finish.
DEADLINE_KILL_WAIT_S = 60


def _stderr_log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


class JobRun:
    """One admitted job from ``mark_running`` to its final state."""

    def __init__(self, engine: "Engine", job: Job):
        self.engine = engine
        self.job = job
        self.parser = StreamParser()
        self.cause: tuple[DeadReason, str] | None = None
        self.ending: Ending | None = None
        self.ended_us: int | None = None
        self.proc = None
        self.deadline_hit = False
        self.reap_result = ReapResult(False, "no session ran; nothing reaped")
        self._finished = False
        self.container_id = ""
        self.container_kill_note = ""
        self._deadline_done = threading.Event()

    # -- helpers --------------------------------------------------------

    @property
    def settings(self) -> Settings:
        return self.engine.settings

    def _env(self) -> dict[str, str]:
        return child_env(live_repos(self.engine.jobs))

    def _fail(self, reason: DeadReason, detail: str) -> None:
        self.cause = (reason, describe(reason, None) + " " + detail)

    # -- phase 1: admit (serial, calling thread) ------------------------

    def admit(self) -> bool:
        """Mark running, make the bundle, bring the container up, assert mounts.

        Returns True when the session may start. A failure here is a dead
        letter with a cause and a bundle, and no session.
        """
        try:
            self._bring_up()
        except ContainerStartError as exc:
            self._fail(DeadReason.CONTAINER_START_FAILED, exc.detail)
        except MountInvalidError as exc:
            self._fail(DeadReason.WORKSPACE_MOUNT_INVALID, exc.detail)
        except BaseException as exc:
            self._fail(
                DeadReason.CONTAINER_START_FAILED,
                f"unexpected {type(exc).__name__}: {exc}",
            )
            self.finish()
            raise
        else:
            return True
        self.finish()
        return False

    def _bring_up(self) -> None:
        engine, job = self.engine, self.job
        self.job = engine.jobs.mark_running(
            job.job_id, container=lifecycle.container_name(job.repo)
        )
        ensure_bundle_dir(job.job_id)
        env = self._env()
        lifecycle.require_workspace(self.settings, job.repo)
        container_id = lifecycle.up(engine.shell, self.settings, job.repo, env)
        self.container_id = container_id
        lifecycle.assert_mounts(
            engine.shell, self.settings, job.repo, container_id, env
        )

    # -- phase 2: the session (worker thread) ---------------------------

    def execute(self) -> None:
        """Run the session. ``finish`` runs from the ``finally``, always."""
        try:
            self._session()
        except BaseException as exc:
            if self.ending is None:
                self._fail(
                    DeadReason.SESSION_LAUNCH_FAILED,
                    f"unexpected {type(exc).__name__}: {exc}",
                )
            raise
        finally:
            self.finish()

    def _signal_tree(self, sig: int) -> None:
        """Signal the host-side process group, not just the ``devcontainer`` CLI.

        Known limit: this skips once the host process has exited (``poll()`` is
        not None). A grandchild that outlives it and holds the pipe is then never
        killed from the host; the reader and drain threads (both daemon) live
        until it exits. Only the container-side pkill on the deadline path can
        reach an in-container process.
        """
        proc = self.proc
        if proc is not None and proc.poll() is None:
            self.engine.shell.killpg(proc, sig)

    def terminate(self) -> None:
        self._signal_tree(signal.SIGTERM)

    def _on_deadline(self) -> None:
        """The wall-clock guard. Runs on the timer thread, never after EOF.

        Kills the host process group, then makes a best effort inside the
        container. It must not depend on the output stream ending: a process
        that still holds the pipe would keep EOF from ever arriving.
        """
        if self.proc is None or self.proc.poll() is not None:
            # The host process already exited by itself (we may be in the reader
            # grace period): this is not a deadline kill, so record nothing.
            self._deadline_done.set()
            return
        self.deadline_hit = True
        try:
            self._signal_tree(signal.SIGKILL)
            self.container_kill_note = lifecycle.kill_in_container(
                self.engine.shell, self.container_id, self.job.task_id, self._env()
            )
        finally:
            self._deadline_done.set()

    def _session(self) -> None:
        job = self.job
        argv = lifecycle.render_exec(
            self.settings, job.repo, job.task_id, job.job_id, job.brief
        )
        if self.engine._stop.is_set():  # a stop arrived during admission
            self._fail(
                DeadReason.KILLED_BY_SIGNAL,
                "a stop was requested during admission; the session never started",
            )
            return
        try:
            self.proc = self.engine.shell.spawn(argv, env=self._env())
        except OSError as exc:
            self.ended_us = self.engine.clock()
            self.ending = Ending(127, stderr_tail=str(exc))
            return
        if self.engine._stop.is_set():  # a stop arrived while spawning
            self.terminate()
        tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
        drain = threading.Thread(
            target=_drain,
            args=(self.proc.stderr, tail),
            daemon=True,
            name=f"drain-{job.job_id}",
        )
        drain.start()
        timer = threading.Timer(self.settings.deadline_s, self._on_deadline)
        timer.daemon = True
        timer.start()
        reader = threading.Thread(
            target=self._read_stream, daemon=True, name=f"reader-{job.job_id}"
        )
        reader.start()
        try:
            returncode = self.proc.wait()
            reader.join(timeout=READER_GRACE_S)
        finally:
            timer.cancel()
            if self.proc.poll() is None:
                self._signal_tree(signal.SIGKILL)
                self.proc.wait()
        drain.join(timeout=DRAIN_GRACE_S)
        self.ended_us = self.engine.clock()  # before any waiting on the kill
        if self.deadline_hit:
            self._deadline_done.wait(DEADLINE_KILL_WAIT_S)
        parser = self.parser
        self.ending = Ending(
            returncode=returncode,
            deadline_hit=self.deadline_hit,
            saw_output=parser.saw_output,
            envelope=parser.envelope,
            unreadable=parser.unreadable,
            parsed_any=parser.parsed_any,
            stderr_tail="\n".join(tail)[-2000:],
        )

    def _read_stream(self) -> None:
        for line in self.proc.stdout:
            session_id = self.parser.feed(line)
            if session_id:
                try:
                    self.engine.jobs.set_session(self.job.job_id, session_id)
                except InvalidJobTransitionError:
                    pass  # the run row, not the job row, is the billing record

    # -- phase 3: everything after the exec -----------------------------

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        try:
            self._finish_steps()
        finally:
            self._idle_check()

    def _decide(self) -> None:
        if self.cause is not None:
            return
        if self.ending is None:
            self._fail(
                DeadReason.SESSION_LAUNCH_FAILED,
                "the dispatcher ended before the session ran",
            )
        else:
            self.cause = classify(self.ending)

    def _finish_steps(self) -> None:
        engine, job = self.engine, self.job
        self._decide()
        started = resolve_now_us(job.started_us)
        ended = max(resolve_now_us(self.ended_us), started)
        record = read_task_record(job.job_id, job.task_id)
        transcript = lifecycle.transcript_path(self.settings, self.parser.session_id)
        write = record_then_collect(
            engine.runs,
            parser=self.parser,
            job=job,
            ended_us=ended,
            failed=self.cause is not None,
            transcript=transcript,
            record=record,
        )
        self._reap(record)
        self._transition(write, ended)
        engine.note(job.job_id, self.reap_result, write)
        if write.record_error is not None:
            raise write.record_error

    def _reap(self, record: dict) -> None:
        engine = self.engine
        try:
            self.reap_result = reap(
                engine.shell,
                self.settings.workspace(self.job.repo),
                worktree_of(record),
                branch_of(record),
                self._env(),
            )
        except Exception as exc:  # a reap problem never fails a job
            self.reap_result = ReapResult(False, f"reap failed: {exc}")

    def _transition(self, write: RunWrite, ended: int) -> None:
        jobs, job_id = self.engine.jobs, self.job.job_id
        if self.cause is None:
            jobs.complete(job_id, outcome=write.outcome.value, now_us=ended)
            return
        reason, detail = self.cause
        if self.container_kill_note:
            detail = f"{detail} [{self.container_kill_note}]"
        if not self.reap_result.reaped:
            detail = f"{detail} [{self.reap_result.note}]"
        jobs.dead_letter(job_id, reason, detail=detail, now_us=ended)

    def _idle_check(self) -> None:
        engine = self.engine
        try:
            lifecycle.stop_if_idle(
                engine.shell, engine.jobs, self.job.repo, self._env()
            )
        except Exception as exc:  # stopping is best effort and never fails a job
            engine.log(f"idle check for {self.job.repo} failed: {exc}")


def _drain(stream, tail: deque) -> None:
    for line in stream:
        tail.append(line.rstrip("\n"))


class Engine:
    """Admit pending jobs one at a time against measured memory until none remain."""

    def __init__(
        self,
        settings: Settings,
        jobs: JobStore,
        runs: RunStore,
        shell: Shell,
        gate: AdmissionGate,
        *,
        clock: Callable[[], int] = now_us,
        log: Callable[[str], None] = _stderr_log,
    ):
        self.settings = settings
        self.jobs = jobs
        self.runs = runs
        self.shell = shell
        self.gate = gate
        self.clock = clock
        self.log = log
        self.errors: list[BaseException] = []
        self.notes: dict[str, dict] = {}
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._active: list[JobRun] = []
        self._memory_failures = 0
        # Reentrant: request_stop runs from a signal handler on the main thread,
        # which may already hold this lock inside _start.
        self._lock = threading.RLock()

    def note(self, job_id: str, reaped: ReapResult, write: RunWrite) -> None:
        self.notes[job_id] = {
            "reap": reaped.note,
            "reaped": reaped.reaped,
            "bundle": str(write.harvest.dir) if write.harvest else "",
            "missing": list(write.harvest.missing) if write.harvest else [],
            "notes": write.notes,
        }

    def request_stop(self) -> None:
        """Admit nothing more and terminate running sessions (they dead-letter).

        Known limit: this and SIGTERM signal only the host process group. The
        container-side ``claude`` keeps running until the idle check stops the
        container, while the job is already labelled KILLED_BY_SIGNAL. A job
        admitted but not yet started is stopped by the check at the top of
        ``JobRun._session``.
        """
        self._stop.set()
        with self._lock:
            for run in list(self._active):
                run.terminate()
        self._wake.set()

    def run(self) -> None:
        threads: list[threading.Thread] = []
        try:
            while not self._stop.is_set() and self._step(threads):
                pass
        finally:
            for thread in threads:
                thread.join()

    def _sleep(self) -> None:
        """Wait for a worker to finish or for the poll interval, whichever is first."""
        self._wake.wait(self.settings.poll_s)
        self._wake.clear()

    def _step(self, threads: list[threading.Thread]) -> bool:
        """One admission attempt. Returns False when there is nothing left to do."""
        threads[:] = [thread for thread in threads if thread.is_alive()]
        pending = pending_jobs(self.jobs)
        if not pending:
            if threads:
                self._sleep()
            return bool(threads)
        if not self._room():
            self.log(
                f"waiting: memory is below the "
                f"{self.gate.min_available_kb // 1024} MB threshold, or unreadable"
            )
            self._sleep()
            return True
        run = JobRun(self, pending[0])
        if run.admit():
            self._start(run, threads)
            self.gate.settle()
        return True

    def _room(self) -> bool:
        """The gate's answer, treating a bad memory read as transient, then fatal.

        A read can see a half-written file; admitting on a guess is never an
        option, so the job simply stays PENDING and the read is retried. After
        ``MEMORY_READ_ATTEMPTS`` consecutive failures the error is raised, which
        ends the run non-zero with the job still PENDING (and reported as such),
        never silently drained.
        """
        try:
            has_room = self.gate.has_room()
        except MemoryReadError as exc:
            self._memory_failures += 1
            self.log(f"memory read failed ({self._memory_failures}): {exc}")
            if self._memory_failures >= MEMORY_READ_ATTEMPTS:
                raise
            return False
        self._memory_failures = 0
        return has_room

    def _start(self, run: JobRun, threads: list[threading.Thread]) -> None:
        with self._lock:
            self._active.append(run)
        thread = threading.Thread(target=self._work, args=(run,), name=run.job.job_id)
        thread.start()
        threads.append(thread)

    def _work(self, run: JobRun) -> None:
        try:
            run.execute()
        except BaseException as exc:  # recorded by finish(); surfaced in the exit code
            self.errors.append(exc)
            self.log(f"job {run.job.job_id} ({run.job.task_id}) raised: {exc!r}")
        finally:
            with self._lock:
                self._active.remove(run)
            self._wake.set()
