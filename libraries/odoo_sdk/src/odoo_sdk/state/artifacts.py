"""The artifact bundle: layout and collector (design section 11, store three).

Layout, keyed by job and never by session::

    <state root>/artifacts/<job_id>/
        task.json        the agent's own record (written in place via the mount)
        session.jsonl    the raw transcript, copied here at run end
        envelope.json    the result envelope as received
        harvest.json     what was collected, what was not, and why

The collector is the subordinate write. Call it **after**
:meth:`odoo_sdk.state.runs_store.RunStore.record_run` has committed, and before
the idle-container check. :func:`collect_artifacts` never raises: every absent,
unreadable or half-written item is recorded in ``missing`` and changes nothing
else. ``bytes`` sums the sizes of the three collected files (``harvest.json``
excluded, because it describes the sum).
"""

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .dispatch_state import bundle_dir, resolve_now_us
from .runs_store import RunStore

TASK_JSON = "task.json"
SESSION_JSONL = "session.jsonl"
ENVELOPE_JSON = "envelope.json"
HARVEST_JSON = "harvest.json"
BUNDLE_FILES = (TASK_JSON, SESSION_JSONL, ENVELOPE_JSON, HARVEST_JSON)


@dataclass(frozen=True)
class Harvest:
    """What :func:`collect_artifacts` found. ``missing`` has one line per gap."""

    job_id: str
    session_id: str
    dir: Path
    has_task_json: bool
    has_session: bool
    has_envelope: bool
    bytes: int
    harvested_us: int
    missing: tuple[str, ...]


def ensure_bundle_dir(job_id: str, root: Path | None = None) -> Path:
    """Create and return ``artifacts/<job_id>/`` (the dispatcher does this at admit)."""
    path = bundle_dir(job_id, root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _replace_atomically(tmp: Path, final: Path) -> None:
    os.replace(tmp, final)


def _store_transcript(directory: Path, source: Path | None) -> str | None:
    """Copy the transcript in. Return a reason when it could not be, else None."""
    if source is None:
        return "no transcript path was given"
    if not source.is_file():
        return f"transcript {source} does not exist"
    tmp = directory / (SESSION_JSONL + ".tmp")
    shutil.copyfile(source, tmp)
    _replace_atomically(tmp, directory / SESSION_JSONL)
    return None


def _store_envelope(directory: Path, envelope: str | None) -> str | None:
    if envelope is None:
        return "no envelope was received"
    tmp = directory / (ENVELOPE_JSON + ".tmp")
    tmp.write_text(envelope, encoding="utf-8")
    _replace_atomically(tmp, directory / ENVELOPE_JSON)
    return None


def _task_json_problem(directory: Path) -> str | None:
    path = directory / TASK_JSON
    try:
        if not path.is_file():
            return "the agent wrote no task.json"
        json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, RecursionError):
        return "task.json is present but is not valid JSON (half-written?)"
    except OSError as exc:
        return f"task.json is unreadable: {exc}"
    return None


def _attempt(label: str, step, missing: list[str]) -> None:
    """Run one collection step; turn any failure into a ``missing`` line."""
    try:
        reason = step()
    except Exception as exc:  # collection must never raise into the caller
        reason = f"{type(exc).__name__}: {exc}"
    if reason:
        missing.append(f"{label}: {reason}")


def _present_size(directory: Path, name: str) -> int | None:
    path = directory / name
    try:
        return path.stat().st_size if path.is_file() else None
    except OSError:
        return None


def collect_artifacts(
    job_id: str,
    *,
    session_id: str = "",
    transcript: Path | None = None,
    envelope: str | None = None,
    runs: RunStore | None = None,
    root: Path | None = None,
    now_us: int | None = None,
) -> Harvest:
    """Fill the job's bundle, write ``harvest.json`` and index it. Never raises.

    ``transcript`` is the tool's own transcript path (copied, not moved);
    ``envelope`` is the raw envelope text as received. ``task.json`` is only
    checked, never written: the agent wrote it through the mount. When ``runs``
    is given the bundle is indexed in ``run_artifacts``. A job that never had a
    session passes ``session_id=""`` and still gets a bundle and an index row.
    Only a ``job_id`` that is not one path component raises (``ValueError``),
    before anything is touched.

    Collection is once per job. A second call rewrites the bundle files and
    ``harvest.json`` but does NOT update the ``run_artifacts`` row, which is
    write-once (``record_artifacts`` returns False and keeps the first index).
    """
    harvested_us = resolve_now_us(now_us)
    directory = bundle_dir(job_id, root)
    missing: list[str] = []
    _attempt(
        "bundle directory",
        lambda: directory.mkdir(parents=True, exist_ok=True),
        missing,
    )
    _attempt(SESSION_JSONL, lambda: _store_transcript(directory, transcript), missing)
    _attempt(ENVELOPE_JSON, lambda: _store_envelope(directory, envelope), missing)
    task_problem = _task_json_problem(directory)
    if task_problem:
        missing.append(f"{TASK_JSON}: {task_problem}")
    sizes = {
        name: _present_size(directory, name)
        for name in (TASK_JSON, SESSION_JSONL, ENVELOPE_JSON)
    }
    harvest = Harvest(
        job_id=job_id,
        session_id=session_id,
        dir=directory,
        has_task_json=sizes[TASK_JSON] is not None and task_problem is None,
        has_session=sizes[SESSION_JSONL] is not None,
        has_envelope=sizes[ENVELOPE_JSON] is not None,
        bytes=sum(size for size in sizes.values() if size is not None),
        harvested_us=harvested_us,
        missing=tuple(missing),
    )
    _attempt("harvest.json", lambda: _write_harvest(harvest), missing)
    if runs is not None:
        _attempt("run_artifacts", lambda: _index(runs, harvest, missing), missing)
    return harvest


def _write_harvest(harvest: Harvest) -> None:
    document = {
        "job_id": harvest.job_id,
        "session_id": harvest.session_id,
        "harvested_us": harvest.harvested_us,
        "has_task_json": harvest.has_task_json,
        "has_session": harvest.has_session,
        "has_envelope": harvest.has_envelope,
        "bytes": harvest.bytes,
        "missing": list(harvest.missing),
    }
    tmp = harvest.dir / (HARVEST_JSON + ".tmp")
    tmp.write_text(json.dumps(document, indent=2), encoding="utf-8")
    _replace_atomically(tmp, harvest.dir / HARVEST_JSON)


def _index(runs: RunStore, harvest: Harvest, missing: list[str]) -> None:
    runs.record_artifacts(
        job_id=harvest.job_id,
        session_id=harvest.session_id,
        dir=str(harvest.dir),
        has_task_json=harvest.has_task_json,
        has_session=harvest.has_session,
        has_envelope=harvest.has_envelope,
        bytes=harvest.bytes,
        harvested_us=harvest.harvested_us,
        missing="\n".join(missing),
    )
