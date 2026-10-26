"""The per-task record: ``task.json``, ``brief.md`` and ``draft-N.md``."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .home import ensure_task_dir, task_dir
from .locks import exclusive

TASK_JSON = "task.json"
BRIEF_MD = "brief.md"
_DRAFT = re.compile(r"^draft-([0-9]+)\.md$")
FRICTION_KEYS = ("tool", "expected", "observed", "did")


@dataclass
class TaskRecord:
    """``sections`` holds every top-level key of task.json except friction."""

    task_id: str
    sections: dict[str, Any] = field(default_factory=dict)
    friction: list[dict[str, Any]] = field(default_factory=list)


def task_json_path(task_id: int | str, home: Path | None = None) -> Path:
    return task_dir(task_id, home) / TASK_JSON


def read_task(task_id: int | str, home: Path | None = None) -> TaskRecord:
    """Read task.json. A missing file gives an empty record."""
    path = task_json_path(task_id, home)
    if not path.exists():
        return TaskRecord(task_id=str(task_id))
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: expected a JSON object")
    friction = doc.pop("friction", [])
    if not isinstance(friction, list):
        raise ValueError(f"{path}: friction must be a list")
    if not all(isinstance(entry, dict) for entry in friction):
        raise ValueError(f"{path}: friction entries must be objects")
    return TaskRecord(task_id=str(task_id), sections=doc, friction=friction)


def _replace_atomically(target: Path, data: bytes) -> None:
    """Write ``data`` to a temp file next to ``target`` and rename it."""
    tmp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=f".{target.name}.", delete=False
        ) as tmp:
            tmp_name = tmp.name
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        if tmp_name:
            Path(tmp_name).unlink(missing_ok=True)
        raise


def write_task(record: TaskRecord, home: Path | None = None) -> Path:
    """Write task.json atomically and return its path."""
    ensure_task_dir(record.task_id, home)
    path = task_json_path(record.task_id, home)
    doc = {**record.sections, "friction": record.friction}
    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    _replace_atomically(path, text.encode("utf-8"))
    return path


def _check_entry(entry: Mapping[str, Any]) -> None:
    for key in FRICTION_KEYS:
        if not isinstance(entry.get(key), str):
            raise ValueError(f"friction entry needs a string {key!r}")


def append_friction(
    task_id: int | str,
    entries: Iterable[Mapping[str, Any]],
    *,
    job_id: str = "",
    home: Path | None = None,
) -> int:
    """Append friction entries under a lock. Return the new entry count."""
    checked = list(entries)
    for entry in checked:
        _check_entry(entry)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with exclusive(f"task-{task_id}", home=home, timeout=5.0):
        record = read_task(task_id, home)
        record.friction.extend(
            {**entry, "job_id": job_id, "recorded_at": stamp} for entry in checked
        )
        write_task(record, home)
    return len(record.friction)


def brief_path(task_id: int | str, home: Path | None = None) -> Path:
    return task_dir(task_id, home) / BRIEF_MD


def draft_paths(task_id: int | str, home: Path | None = None) -> list[Path]:
    """Return the existing draft files, sorted by number."""
    directory = task_dir(task_id, home)
    if not directory.is_dir():
        return []
    found = []
    for child in directory.iterdir():
        match = _DRAFT.fullmatch(child.name)
        if match:
            found.append((int(match.group(1)), child))
    return [path for _, path in sorted(found)]


def next_draft_path(task_id: int | str, home: Path | None = None) -> Path:
    """Create the next empty ``draft-N.md``. A number is never reused."""
    directory = ensure_task_dir(task_id, home)
    existing = draft_paths(task_id, home)
    number = int(_DRAFT.fullmatch(existing[-1].name).group(1)) + 1 if existing else 1
    while True:
        path = directory / f"draft-{number}.md"
        try:
            with open(path, "x"):
                return path
        except FileExistsError:
            number += 1


def approve_draft(task_id: int | str, draft: Path, home: Path | None = None) -> Path:
    """Copy a non-empty draft of this task onto ``brief.md``."""
    if draft not in draft_paths(task_id, home):
        raise ValueError(f"{draft} is not a draft of task {task_id}")
    data = draft.read_bytes()
    if not data:
        raise ValueError(f"{draft} is empty")
    target = brief_path(task_id, home)
    _replace_atomically(target, data)
    return target
