"""Reading a session's stream, writing its run row, then collecting artifacts.

The order is the point, and it is the order of design sections 7 and 10:

1. the envelope is parsed (:class:`StreamParser`),
2. the run row is written (:func:`record_then_collect`),
3. artifacts are collected, after the run row has committed,
4. only then may the caller run the idle check, because a container that
   stops takes the transcript with it.

Session statistics outrank every other artifact. Absence of an artifact is
recorded in ``missing`` and changes nothing else; a collection failure cannot
touch what was already recorded.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from odoo_sdk.state.artifacts import Harvest, TASK_JSON, collect_artifacts
from odoo_sdk.state.dispatch_state import bundle_dir
from odoo_sdk.state.runs_store import RunOutcome, RunStore, map_reported_outcome

from .schema import question_count, usable_structured

UNKNOWN_TOOL_VERSION = "unknown"


class StreamParser:
    """Incremental reader of ``--output-format stream-json`` lines.

    ``feed`` returns the session id the first time one is seen, so the caller
    can record it on the job immediately.
    """

    def __init__(self) -> None:
        self.session_id = ""
        self.tool_version = ""
        self.envelope: dict | None = None
        self.envelope_text: str | None = None
        self.saw_output = False
        self.unreadable = False

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
        if event.get("type") == "result":
            self.envelope, self.envelope_text = event, text
        return self._remember(event)

    def _remember(self, event: dict) -> str | None:
        if version := event.get("claude_code_version"):
            self.tool_version = str(version)
        sid = event.get("session_id")
        if sid and not self.session_id:
            self.session_id = str(sid)
            return self.session_id
        return None


def read_task_record(job_id: str, root: Path | None = None) -> dict:
    """The agent's own ``task.json`` from the bundle, or ``{}`` when unusable."""
    try:
        data = json.loads((bundle_dir(job_id, root) / TASK_JSON).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


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
class RunWrite:
    """What :func:`record_then_collect` did."""

    recorded: bool = False
    harvest: Harvest | None = None
    record_error: BaseException | None = None
    outcome: RunOutcome = RunOutcome.FAILED
    notes: list[str] = field(default_factory=list)


def stored_outcome(envelope: dict | None, failed: bool) -> RunOutcome:
    """The one mapping lives in odoo_sdk; a dead letter is stored as FAILED."""
    structured = usable_structured(envelope)
    if failed or structured is None:
        return RunOutcome.FAILED
    return map_reported_outcome(structured["outcome"], question_count(structured))


def record_then_collect(
    runs: RunStore,
    *,
    parser: StreamParser,
    job,
    ended_us: int,
    failed: bool,
    transcript: Path | None,
    record: dict,
    root: Path | None = None,
) -> RunWrite:
    """Write the run row, then collect artifacts. Neither step may block the other.

    A job with no session id gets no run row (design section 7) but still gets
    a bundle. If the run write itself fails, collection still runs and the
    error is returned for the caller to surface once everything else is done.
    """
    write = RunWrite(outcome=stored_outcome(parser.envelope, failed))
    if parser.session_id:
        try:
            write.recorded = runs.record_run(
                session_id=parser.session_id,
                job_id=job.job_id,
                task_id=job.task_id,
                # The job's start is mark_running, so this interval includes any
                # cold container start this task caused: that time was spent on it.
                started_us=job.started_us,
                ended_us=max(ended_us, job.started_us),
                outcome=write.outcome,
                tool_version=parser.tool_version or UNKNOWN_TOOL_VERSION,
                repo=job.repo,
                branch=branch_of(record),
                pr_num=pr_number_of(record),
                transcript=str(transcript or ""),
                brief=job.brief,
                envelope=parser.envelope_text,
            )
        except Exception as exc:  # billing evidence: surfaced, never swallowed
            write.record_error = exc
    try:
        write.harvest = collect_artifacts(
            job.job_id,
            session_id=parser.session_id,
            transcript=transcript,
            envelope=parser.envelope_text,
            runs=runs,
            root=root,
        )
    except Exception as exc:  # collection is subordinate: it changes nothing else
        write.notes.append(f"collection failed: {type(exc).__name__}: {exc}")
    return write
