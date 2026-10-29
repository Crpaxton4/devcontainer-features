"""Reading a session's stream, recording its result, then collecting the bundle.

The order is the point, and it is the order of design sections 7, 10 and 17:

1. the envelope is parsed (:class:`StreamParser`),
2. the result is written to the job row (:func:`record_then_collect`),
3. the bundle (``tasks/<task_id>/jobs/<job_id>/``) is collected and indexed on
   the same row, once the row is finished,
4. only then may the caller run the idle check, because a container that
   stops takes the transcript with it.

The job row is the only run record. Its ``started_us``/``ended_us`` window is
the billable one: ``mark_running`` opens the row at admission and
``mark_spawned`` moves ``started_us`` to the moment the session came up, so a
cold container start is never billed. This module never moves it again.

Absence of an artifact is recorded in ``missing`` and changes nothing else; a
collection failure cannot touch what was already recorded.
"""

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from odoo_dev_data.home import ensure_job_bundle_dir, task_dir
from odoo_dev_data.jobs import JobStore, RunOutcome, map_reported_outcome
from odoo_dev_data.tasks import read_task

from .schema import question_count, usable_structured

UNKNOWN_TOOL_VERSION = "unknown"
SESSION_JSONL = "session.jsonl"
ENVELOPE_JSON = "envelope.json"
TASK_JSON = "task.json"
#: The ``parent_tool_use_id`` stream-json gives every event of a forked command
#: (``context: fork``) is this prefix and the command name. Observed on Claude
#: Code 2.1.295, not documented: if it changes, nothing is adopted and the run is
#: a STRUCTURED_OUTPUT_MISSING dead letter, as before.
FORK_PARENT_PREFIX = "forked-command-"
STRUCTURED_TOOL = "StructuredOutput"


class StreamParser:
    """Incremental reader of ``--output-format stream-json`` lines.

    ``feed`` returns the session id the first time one is seen, so the caller
    can record it on the job immediately.

    The entry is a forked command, so the run answers inside the fork, and the
    result envelope has no ``structured_output`` of its own. The parser keeps the
    entry fork's accepted ``StructuredOutput`` input and puts it in the envelope
    when the envelope has none. ``usable_structured`` still checks the value.
    """

    def __init__(self, entry: str = "") -> None:
        self.session_id = ""
        self.tool_version = ""
        self.envelope: dict | None = None
        self.envelope_text: str | None = None
        self.saw_output = False
        self.unreadable = False
        self.parsed_any = False
        self.fork_parent = FORK_PARENT_PREFIX + entry.lstrip("/") if entry else ""
        self.fork_output: dict | None = None
        self._pending: dict[str, object] = {}

    def feed(self, line: str) -> str | None:
        text = line.strip()
        if not text:
            return None
        self.saw_output = True
        try:
            event = json.loads(text)
        except ValueError:
            if '"type":"result"' in text.replace(" ", ""):
                self.unreadable = True
            return None
        if not isinstance(event, dict):
            return None
        self.parsed_any = True
        if event.get("type") == "result":
            if event.get("structured_output") is None and self.fork_output:
                event["structured_output"] = self.fork_output
                text = json.dumps(event)
            self.envelope, self.envelope_text = event, text
        elif self.fork_parent and event.get("parent_tool_use_id") == self.fork_parent:
            self._watch_fork(event)
        return self._remember(event)

    def _watch_fork(self, event: dict) -> None:
        """Keep a fork ``StructuredOutput`` input once its tool result has no error."""
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else ():
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == STRUCTURED_TOOL:
                self._pending[str(block.get("id"))] = block.get("input")
            elif block.get("type") == "tool_result" and not block.get("is_error"):
                value = self._pending.pop(str(block.get("tool_use_id")), None)
                if isinstance(value, dict):
                    self.fork_output = value

    def _remember(self, event: dict) -> str | None:
        if version := event.get("claude_code_version"):
            self.tool_version = str(version)
        sid = event.get("session_id")
        if sid and not self.session_id:
            self.session_id = str(sid)
            return self.session_id
        return None


def read_task_record(task_id: str, home: Path) -> dict:
    """The agent's own ``task.json`` sections, or ``{}`` when missing or unusable."""
    try:
        return read_task(task_id, home).sections
    except (OSError, ValueError):
        return {}


def _section(record: dict, name: str) -> dict:
    value = record.get(name)
    return value if isinstance(value, dict) else {}


def branch_of(record: dict) -> str:
    value = _section(record, "build").get("branch")
    return value if isinstance(value, str) else ""


def worktree_of(record: dict) -> str:
    value = _section(record, "build").get("worktree")
    return value if isinstance(value, str) else ""


def pr_number_of(record: dict) -> int:
    """Read ``pr.number`` if present; 0 when it is not.

    No ``pr`` section is defined in task.json today (artifact.sh has no pr
    stage), so this is deliberately not a guess: absent means ``pr_num`` 0.
    """
    pr = record.get("pr")
    candidates = [pr] if isinstance(pr, int) else []
    if isinstance(pr, dict):
        candidates += [pr.get("number"), pr.get("num")]
    for candidate in candidates:
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            return max(candidate, 0)
    return 0


@dataclass
class Bundle:
    """What :func:`collect_bundle` found. ``missing`` has one line per gap."""

    dir: Path
    has_task_json: bool
    has_session: bool
    has_envelope: bool
    bytes: int
    missing: list[str]


@dataclass
class RunWrite:
    """What :func:`record_then_collect` did."""

    recorded: bool = False
    bundle: Bundle | None = None
    record_error: BaseException | None = None
    outcome: RunOutcome = RunOutcome.FAILED
    notes: list[str] = field(default_factory=list)


def stored_outcome(envelope: dict | None, failed: bool) -> RunOutcome:
    """The one mapping lives in odoo_dev_data.jobs; a dead letter is stored as FAILED."""
    structured = usable_structured(envelope)
    if failed or structured is None:
        return RunOutcome.FAILED
    return map_reported_outcome(structured["outcome"], question_count(structured))


def envelope_usage(envelope: dict | None) -> tuple[int, int, float]:
    """``(input_tokens, output_tokens, cost_usd)`` from the envelope; 0 when absent."""
    envelope = envelope or {}
    usage = envelope.get("usage")
    usage = usage if isinstance(usage, dict) else {}

    def number(source: dict, key: str, kind):
        value = source.get(key)
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        return kind(value) if ok else kind(0)

    return (
        number(usage, "input_tokens", int),
        number(usage, "output_tokens", int),
        number(envelope, "total_cost_usd", float),
    )


def _copy_atomically(source: Path, target: Path) -> None:
    tmp = target.with_name(target.name + ".tmp")
    shutil.copyfile(source, tmp)
    os.replace(tmp, target)


def _write_atomically(target: Path, text: str) -> None:
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)


def _attempt(label: str, step, missing: list[str]) -> bool:
    """Run one collection step; a failure becomes a ``missing`` line. True on success."""
    try:
        reason = step()
    except Exception as exc:  # collection must never raise into the caller
        reason = f"{type(exc).__name__}: {exc}"
    if reason:
        missing.append(f"{label}: {reason}")
    return not reason


def _store_transcript(directory: Path, source: Path | None) -> str | None:
    if source is None:
        return "no transcript path was given"
    if not source.is_file():
        return f"transcript {source} does not exist"
    _copy_atomically(source, directory / SESSION_JSONL)
    return None


def _store_envelope(directory: Path, envelope: str | None) -> str | None:
    if not envelope:
        return "no envelope was received"
    _write_atomically(directory / ENVELOPE_JSON, envelope)
    return None


def _size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def collect_bundle(
    job, *, transcript: Path | None, envelope: str | None, home: Path
) -> Bundle | None:
    """Fill ``tasks/<task_id>/jobs/<job_id>/`` and describe it. Never raises.

    The transcript is copied to ``session.jsonl`` and the envelope text written
    to ``envelope.json``. ``task.json`` is only looked for at
    ``tasks/<task_id>/task.json``: the agent writes it itself. Returns None when
    the bundle directory cannot be made.
    """
    missing: list[str] = []
    made: list[Path] = []
    if not _attempt(
        "bundle directory",
        lambda: made.append(ensure_job_bundle_dir(job.task_id, job.job_id, home)),
        missing,
    ):
        return None
    directory = made[0]
    has_session = _attempt(
        SESSION_JSONL, lambda: _store_transcript(directory, transcript), missing
    )
    has_envelope = _attempt(
        ENVELOPE_JSON, lambda: _store_envelope(directory, envelope), missing
    )
    has_task_json = (task_dir(job.task_id, home) / TASK_JSON).is_file()
    if not has_task_json:
        missing.append(f"{TASK_JSON}: the agent wrote no task.json")
    size = _size(directory / SESSION_JSONL) + _size(directory / ENVELOPE_JSON)
    return Bundle(directory, has_task_json, has_session, has_envelope, size, missing)


def index_collected(jobs: JobStore, job_id: str, write: RunWrite) -> None:
    """Index the collected bundle on the finished job row. Never raises.

    Call it once the job is DONE or DEAD_LETTER. A failure is noted on ``write``.
    """
    bundle = write.bundle
    if bundle is None:
        return
    try:
        jobs.index_bundle(
            job_id,
            bundle_dir=str(bundle.dir),
            has_task_json=bundle.has_task_json,
            has_session=bundle.has_session,
            has_envelope=bundle.has_envelope,
            bytes=bundle.bytes,
            missing="\n".join(bundle.missing),
        )
    except Exception as exc:
        write.notes.append(f"bundle index failed: {type(exc).__name__}: {exc}")


def record_then_collect(
    jobs: JobStore,
    *,
    parser: StreamParser,
    job,
    started_us: int,
    ended_us: int,
    failed: bool,
    transcript: Path | None,
    record: dict,
    home: Path,
) -> RunWrite:
    """Record the result on the job row, then collect the bundle.

    A success moves the row RUNNING to DONE with the result, then indexes the
    bundle. A failure writes the bundle files only: the caller dead-letters the
    row and then calls :func:`index_collected`, because ``index_bundle`` needs a
    finished row. If the result write fails, collection still runs and the error
    is returned for the caller to surface once everything else is done.
    ``started_us`` only bounds ``ended_us``; the row keeps its own start.
    """
    write = RunWrite(outcome=stored_outcome(parser.envelope, failed))
    structured = usable_structured(parser.envelope)
    if not failed:
        tokens_in, tokens_out, cost = envelope_usage(parser.envelope)
        try:
            jobs.record_result(
                job.job_id,
                outcome=write.outcome,
                session_id=parser.session_id,
                branch=branch_of(record),
                pr_num=pr_number_of(record),
                tool_version=parser.tool_version or UNKNOWN_TOOL_VERSION,
                envelope=parser.envelope_text,
                transcript_path=str(transcript or ""),
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                cost_usd=cost,
                question_count=question_count(structured) if structured else None,
                now_us=max(ended_us, started_us),
            )
            write.recorded = True
        except Exception as exc:  # billing evidence: surfaced, never swallowed
            write.record_error = exc
    write.bundle = collect_bundle(
        job, transcript=transcript, envelope=parser.envelope_text, home=home
    )
    if write.bundle is None:
        write.notes.append("collection failed: the bundle directory was not made")
    if write.recorded:
        index_collected(jobs, job.job_id, write)
    return write
