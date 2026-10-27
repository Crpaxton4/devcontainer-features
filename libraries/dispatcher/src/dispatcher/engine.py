"""The admission loop and one job's life (design sections 7, 8, 10 and 17).

Admission is serial and happens on the calling thread: measure, admit, bring
the project's container up and assert its mounts, let the machine settle, and
only then measure again. The long part, the session, runs on a worker thread.

Everything after the exec runs from :meth:`JobRun.finish`, which is reached
from a ``finally`` on every path, because the expensive cases are the ones
that crash. The order inside it is fixed:

1. decide the cause (or success) from what the dispatcher saw,
2. write the result to the job row, then collect the bundle
   (``collect.record_then_collect``),
3. reap the worktree if, and only if, it is provably pushed,
4. move the job to DEAD_LETTER (a success was moved to DONE in step 2),
   then index the bundle on the row,
5. only then the idle check, which may stop the container.
"""

import signal
import sys
import threading
from collections import deque
from collections.abc import Callable

import usage_log
from odoo_dev_data.db import now_us, resolve_now_us
from odoo_dev_data.jobs import (
    DeadReason,
    InvalidJobTransitionError,
    Job,
    JobStore,
)

from . import lifecycle
from .collect import (
    RunWrite,
    StreamParser,
    branch_of,
    index_collected,
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
from .queue import pending_jobs
from .reap import ReapResult, reap
from .settings import Settings

#: ``result``-event keys copied verbatim into the job usage line, and the keys of
#: its ``usage`` object (Claude Code's ``claude -p --output-format stream-json``).
_ENVELOPE_USAGE_KEYS = ("total_cost_usd", "num_turns", "duration_ms")
_ENVELOPE_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def job_usage_context(job, parser: StreamParser, ok: bool) -> dict:
    """The context of a job's usage line: ids plus the envelope's own figures."""
    envelope = parser.envelope or {}
    context = {
        "job_id": job.job_id,
        "session_id": parser.session_id or job.session_id,
        "source": "dispatcher",
        "ok": ok,
    }
    usage = envelope.get("usage")
    if isinstance(usage, dict):
        context.update({k: usage[k] for k in _ENVELOPE_TOKEN_KEYS if k in usage})
    context.update({k: envelope[k] for k in _ENVELOPE_USAGE_KEYS if k in envelope})
    return context


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
        self.spawned_us: int | None = None  # the session spawn; None before a spawn
        self.proc = None
        self.deadline_hit = False
        self.reap_result = ReapResult(False, "no session ran; nothing reaped")
        self._finished = False
        self.container_id = ""
        self.container_kill_note = ""
        self.stop_requested = False  # set by terminate; acted on by the run's thread
        self._deadline_done = threading.Event()

    # -- helpers --------------------------------------------------------

    @property
    def settings(self) -> Settings:
        return self.engine.settings

    def _env(self) -> dict[str, str]:
        return child_env()

    def _fail(self, reason: DeadReason, detail: str) -> None:
        self.cause = (reason, describe(reason, None) + " " + detail)

    # -- phase 1: admit (serial, calling thread) ------------------------

    def admit(self) -> bool:
        """Mark running, make the bundle, bring the container up, assert mounts,
        wait until postgres and odoo answer.

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
        env = self._env()
        lifecycle.require_workspace(self.settings, job.repo)
        container_id = lifecycle.up(engine.shell, self.settings, job.repo, env)
        self.container_id = container_id
        lifecycle.assert_mounts(
            engine.shell, self.settings, job.repo, container_id, env
        )
        lifecycle.wait_ready(engine.shell, container_id, env)

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
        until it exits. Both kill paths (the deadline and a stop request) also
        reach the container side through ``kill_in_container``, a best-effort
        ``pkill`` whose exit is not verified.
        """
        proc = self.proc
        if proc is not None and proc.poll() is None:
            self.engine.shell.killpg(proc, sig)

    def terminate(self) -> None:
        """Stop request: signal the host group now, kill in the container later.

        This runs from the signal handler, so it must not block. The blocking
        ``docker exec`` is left to the run's own thread (``_kill_after_stop``).
        """
        proc = self.proc
        if proc is not None and proc.poll() is None:
            self.stop_requested = True
        self._signal_tree(signal.SIGTERM)

    def _kill_after_stop(self) -> None:
        """Run on the run's thread once the host process exits after a stop.

        The deadline path, if it fired, has its own container kill and note.
        """
        if not self.stop_requested or self.deadline_hit or self.container_kill_note:
            return
        self.container_kill_note = lifecycle.kill_in_container(
            self.engine.shell,
            self.container_id,
            self.job.task_id,
            self._env(),
            lifecycle.job_entry(self.job, self.settings),
        )

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
                self.engine.shell,
                self.container_id,
                self.job.task_id,
                self._env(),
                lifecycle.job_entry(self.job, self.settings),
            )
        finally:
            self._deadline_done.set()

    def _session(self) -> None:
        job = self.job
        argv = lifecycle.render_exec(
            self.settings,
            job.repo,
            job.task_id,
            job.job_id,
            job.brief,
            entry=lifecycle.job_entry(job, self.settings),
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
        self.spawned_us = self.engine.clock()
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
            self._kill_after_stop()
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
                    pass  # the row already finished; the result write records the session

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
        # No spawn happened (a dead letter in admission): fall back to the admit time.
        spawned = self.spawned_us if self.spawned_us is not None else job.started_us
        started = resolve_now_us(spawned)
        ended = max(resolve_now_us(self.ended_us), started)
        record = read_task_record(job.task_id, self.settings.home)
        transcript = lifecycle.transcript_path(self.settings, self.parser.session_id)
        write = record_then_collect(
            engine.jobs,
            parser=self.parser,
            job=job,
            started_us=started,
            ended_us=ended,
            failed=self.cause is not None,
            transcript=transcript,
            record=record,
            home=self.settings.home,
        )
        self._reap(record)
        self._transition(write, ended)
        self._record_usage()
        engine.note(job.job_id, self.reap_result, write)
        if write.record_error is not None:
            raise write.record_error

    def _record_usage(self) -> None:
        """Write the one ``job`` usage line for this finished job (#1204).

        The token and cost figures are copied from the ``result`` envelope the
        parser kept, never calculated. Best effort: a failing usage log must not
        fail the job, so every error is swallowed here (``usage_log`` already
        drops a failed write itself).
        """
        try:
            usage_log.record(
                "job",
                lifecycle.job_entry(self.job, self.settings),
                **job_usage_context(self.job, self.parser, self.cause is None),
            )
        except Exception as exc:
            self.engine.log(f"usage line for {self.job.job_id} failed: {exc}")

    def _reap(self, record: dict) -> None:
        engine = self.engine
        try:
            self.reap_result = reap(
                engine.shell,
                self.settings,
                self.job.repo,
                worktree_of(record),
                branch_of(record),
                self._env(),
            )
        except Exception as exc:  # a reap problem never fails a job
            self.reap_result = ReapResult(False, f"reap failed: {exc}")

    def _transition(self, write: RunWrite, ended: int) -> None:
        jobs, job_id = self.engine.jobs, self.job.job_id
        if self.cause is None:
            return  # record_then_collect moved the row to DONE
        reason, detail = self.cause
        if self.container_kill_note:
            detail = f"{detail} [{self.container_kill_note}]"
        if not self.reap_result.reaped:
            detail = f"{detail} [{self.reap_result.note}]"
        jobs.dead_letter(job_id, reason, detail=detail, now_us=ended)
        index_collected(jobs, job_id, write)

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
        shell: Shell,
        gate: AdmissionGate,
        *,
        clock: Callable[[], int] = now_us,
        log: Callable[[str], None] = _stderr_log,
    ):
        self.settings = settings
        self.jobs = jobs
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
        self._last_admit_us = self.clock()  # reset by run() and by each admission
        # Reentrant: request_stop runs from a signal handler on the main thread,
        # which may already hold this lock inside _start.
        self._lock = threading.RLock()

    def note(self, job_id: str, reaped: ReapResult, write: RunWrite) -> None:
        self.notes[job_id] = {
            "reap": reaped.note,
            "reaped": reaped.reaped,
            "bundle": str(write.bundle.dir) if write.bundle else "",
            "missing": list(write.bundle.missing) if write.bundle else [],
            "notes": write.notes,
        }

    def request_stop(self) -> None:
        """Admit nothing more and terminate running sessions (they dead-letter).

        Each run gets SIGTERM on its host process group. The run's own thread
        then makes the same best-effort container-side ``pkill`` as the deadline
        path, once the host process exits, and puts its note in the dead-letter
        detail. The handler never waits on ``docker``. A job admitted but not yet
        started is stopped by the check at the top of ``JobRun._session``.
        """
        self._stop.set()
        with self._lock:
            for run in list(self._active):
                run.terminate()
        self._wake.set()

    def run(self) -> None:
        threads: list[threading.Thread] = []
        self._last_admit_us = self.clock()
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
            if self._waited_too_long(threads):
                return False
            self.log(
                f"waiting: memory is below the "
                f"{self.gate.min_available_kb // 1024} MB threshold, or unreadable"
            )
            self._sleep()
            return True
        self._last_admit_us = self.clock()
        run = JobRun(self, pending[0])
        if run.admit():
            self._start(run, threads)
            self.gate.settle()
        return True

    def _waited_too_long(self, threads: list[threading.Thread]) -> bool:
        """True, after logging why, when nothing has been admitted for too long.

        The timer only runs while no job is running: a running job holds the
        memory and its end can free it, so that wait is not a stall. The run
        then ends with the jobs still PENDING, and ``cli`` exits 1.
        """
        now = self.clock()
        if threads:
            self._last_admit_us = now
            return False
        limit_s = self.settings.admit_timeout_s
        if now - self._last_admit_us < limit_s * 1_000_000:
            return False
        last = self.gate.last_available_kb
        figure = "no readable figure" if last is None else f"{last // 1024} MB"
        self.log(
            f"dispatch: stopping, nothing was admitted for {limit_s:g} s: available "
            f"memory is below the {self.gate.min_available_kb // 1024} MB threshold "
            f"(last measured: {figure}); unadmitted jobs remain PENDING. Stop an "
            "idle editor stack, then rerun `dispatch run` to resume"
        )
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
