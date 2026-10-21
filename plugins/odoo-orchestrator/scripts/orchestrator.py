#!/usr/bin/env python3
"""Host-side helpers for /odoo-orchestrator:run. Standard library only.

    plan-path <task>           create a new draft file for a task; print its path
    approve <task> <path>      record the path the user approved for a task
    lint <brief>               a backstop for obvious breaches of the requirement line
    unhandled                  stdin: dead-letters JSON; print the unhandled ones
    handled <job_id>...        record dead letters that are dealt with
    friction [report.json]     append friction not yet written; print the count
    records [report.json]      print the batch as a table
    batch-add <batch_id>       remember a batch that dispatch.sh add submitted
    batches                    print the remembered batch ids not yet processed
    batch-done <batch_id>      mark a batch processed

``report.json`` is the object ``dispatch.sh report <batch_id>`` prints; without a
file it is read from stdin. A job with no ``bundle`` has no envelope: no friction
and blank figures. The home directory is ``$ODOO_ORCHESTRATOR_HOME`` (made
absolute) or ``~/.local/state/odoo-orchestrator``. It holds
``briefs/<task>/draft-<n>.md``, ``approved/<task>.txt``, ``handled.txt``,
``friction-done.txt``, ``batches.txt``, ``batches-done.txt`` and
``friction/<date>.md``. Nothing here writes a timesheet.

The linter is not the enforcement. The requirement line is semantic, and the
command text carries it.
"""

import json
import os
import re
import sys
from datetime import date
from pathlib import Path

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
#: The dispatcher refuses a larger prompt; flag it before the call does.
MAX_BRIEF_BYTES = 100_000

_EXT = (
    r"py|xml|csv|js|ts|tsx|jsx|scss|css|html|sql|json|yaml|yml|sh|conf|cfg|toml|md|txt"
)
_CODE_EXT = r"py|xml|js|ts|tsx|jsx|scss|sql|sh|yaml|yml|conf|toml"
_COMMANDS =r"git|docker|devcontainer|odoo-bin|odoo|pytest|uv|pip|npm|bash|sh|make|psql|curl"

# Obvious cases only. A module, model or method name has no lexical marker, so
# a brief can pass every rule here and still prescribe the change.
LINT_RULES = (
    ("a code block", re.compile(r"^\s*(```|~~~)")),
    ("a shell prompt line", re.compile(r"^\s*\$\s+\S")),
    ("a file path", re.compile(rf"(?<![\w/:.-])(?:\.{{0,2}}/)?(?:[\w.-]+/)+[\w.-]+\.(?:{_EXT})\b")),
    # A bare name is flagged only for code-like extensions. md, csv, txt and json
    # stay exempt because they are business words ("totals.csv export"), and
    # node.js is a product name.
    ("a file name", re.compile(rf"(?<![\w/:.-])(?!node\.js\b)[\w-]+(?:\.[\w-]+)*\.(?:{_CODE_EXT})\b")),
    ("a command", re.compile(rf"`(?:{_COMMANDS})\s[^`]*`")),
    ("a command", re.compile(r"\b(?:odoo-bin|docker compose|devcontainer up|pytest)\b")),
    # Upper case only: "select a customer from the list" is ordinary prose.
    ("SQL", re.compile(r"\b(?:SELECT\s.+\sFROM|INSERT\s+INTO|DELETE\s+FROM|UPDATE\s+\w+\s+SET)\b")),
    ("an XPath", re.compile(r"//\w+\[|\bxpath\b", re.I)),
)


def home() -> Path:
    override = os.environ.get("ODOO_ORCHESTRATOR_HOME")
    if override:
        return Path(os.path.abspath(os.path.expanduser(override)))
    return Path.home() / ".local/state/odoo-orchestrator"


def _check_task(task: str) -> None:
    if not NAME.match(task):
        raise ValueError(f"task must be one plain name: {task!r}")


def plan_path(task: str) -> Path:
    """Create the next draft file for a task. Never reuses a name."""
    _check_task(task)
    folder = home() / "briefs" / task
    folder.mkdir(parents=True, exist_ok=True)
    numbers = [
        int(m.group(1)) for p in folder.glob("draft-*.md") if (m := re.fullmatch(r"draft-([0-9]+)\.md", p.name))
    ]
    number = max(numbers, default=0) + 1
    while True:
        path = folder / f"draft-{number}.md"
        try:
            with path.open("x"):
                return path
        except FileExistsError:
            number += 1


def approve(task: str, path: str) -> None:
    """Record the brief the user approved for a task (the latest approval wins)."""
    _check_task(task)
    brief = Path(path).resolve()
    drafts = (home() / "briefs" / task).resolve()
    if drafts not in brief.parents:
        raise ValueError(f"{path} is not a draft of task {task} (expected under {drafts})")
    if not brief.is_file() or brief.stat().st_size == 0:
        raise ValueError(f"the brief is missing or empty: {path}")
    folder = home() / "approved"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{task}.txt").write_text(str(Path(path).resolve()) + "\n", encoding="utf-8")


def approved_brief(task: str) -> str:
    """The brief approved for a task, or empty when none or the file is gone."""
    try:
        text = (home() / "approved" / f"{task}.txt").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    path = text.strip()
    return path if path and Path(path).is_file() else ""


def lint(text: str) -> list[str]:
    hits = []
    if len(text.encode()) > MAX_BRIEF_BYTES:
        hits.append(f"the brief is over {MAX_BRIEF_BYTES} bytes; the dispatcher refuses it")
    for number, line in enumerate(text.splitlines(), 1):
        for label, rule in LINT_RULES:
            if rule.search(line):
                hits.append(f"line {number}: {label}: {line.strip()[:100]}")
    return hits


def _ids(name: str) -> set[str]:
    try:
        return set((home() / name).read_text(encoding="utf-8", errors="replace").split())
    except OSError:
        return set()


def _append_ids(name: str, ids: list[str]) -> None:
    home().mkdir(parents=True, exist_ok=True)
    with (home() / name).open("a", encoding="utf-8") as handle:
        handle.write("".join(f"{i}\n" for i in ids))


def unhandled(letters: list[dict]) -> list[dict]:
    seen = _ids("handled.txt")
    out = []
    for letter in letters:
        if letter.get("job_id") in seen:
            continue
        task = str(letter.get("task", ""))
        out.append({**letter, "approved_brief": approved_brief(task) if NAME.match(task) else ""})
    return out


def mark_handled(job_ids: list[str]) -> None:
    _append_ids("handled.txt", job_ids)


BATCH_ID = re.compile(r"^[0-9a-f]{32}$")


def _check_batch(batch_id: str) -> None:
    if not BATCH_ID.match(batch_id):
        raise ValueError(f"batch id must be 32 hex digits: {batch_id!r}")


def batch_add(batch_id: str) -> None:
    """Remember a batch that `dispatch.sh add` returned. Adding twice is harmless."""
    _check_batch(batch_id)
    if batch_id not in _ids("batches.txt"):
        _append_ids("batches.txt", [batch_id])


def batches() -> list[str]:
    """Remembered batch ids not yet processed, oldest first."""
    try:
        order = (home() / "batches.txt").read_text(encoding="utf-8", errors="replace").split()
    except OSError:
        return []
    done = _ids("batches-done.txt")
    return [b for b in dict.fromkeys(order) if b not in done]


def batch_done(batch_id: str) -> None:
    _check_batch(batch_id)
    _append_ids("batches-done.txt", [batch_id])


def _envelope(job: dict) -> dict:
    bundle = job.get("bundle")
    if not bundle or not isinstance(bundle, str):
        return {}
    try:
        data = json.loads((Path(bundle) / "envelope.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _structured(job: dict) -> dict:
    value = _envelope(job).get("structured_output")
    return value if isinstance(value, dict) else {}


def friction(report: dict, today: date | None = None) -> int:
    """Append friction of jobs not yet written; return how many entries."""
    done = _ids("friction-done.txt")
    lines, count, seen = [], 0, []
    for job in report.get("jobs", []):
        job_id = job.get("job_id")
        if not job_id or job_id in done:
            continue
        seen.append(job_id)
        for entry in _structured(job).get("friction") or []:
            count += 1
            lines += [
                f"## task {job.get('task')} job {job_id}",
                f"- tool: {entry.get('tool', '')}",
                f"- expected: {entry.get('expected', '')}",
                f"- observed: {entry.get('observed', '')}",
                f"- did: {entry.get('did', '')}",
                "",
            ]
    if lines:
        path = home() / "friction" / f"{(today or date.today()).isoformat()}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    if seen:
        _append_ids("friction-done.txt", seen)
    return count


def records(report: dict) -> str:
    head = "| task | repo | round | state | outcome | session | seconds | turns | cost_usd | summary |"
    rows = [head, "|" + " --- |" * 10]
    for job in report.get("jobs", []):
        env = _envelope(job)
        seconds = env.get("duration_ms")
        summary = _structured(job).get("summary") or job.get("dead_reason") or ""
        cells = [
            job.get("task"),
            job.get("repo"),
            job.get("round"),
            job.get("state"),
            job.get("outcome"),
            job.get("session_id"),
            round(seconds / 1000) if isinstance(seconds, (int, float)) else "",
            env.get("num_turns", ""),
            env.get("total_cost_usd", ""),
            str(summary).replace("|", "/").replace("\n", " "),
        ]
        rows.append("| " + " | ".join("" if c is None else str(c) for c in cells) + " |")
    return "\n".join(rows)


def _report(args: list[str]) -> dict:
    text = Path(args[0]).read_text(encoding="utf-8") if args else sys.stdin.read()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("the run JSON is not an object")
    return data


def _dispatch(command: str, args: list[str]) -> int:
    if command == "plan-path" and len(args) == 1:
        print(plan_path(args[0]))
    elif command == "approve" and len(args) == 2:
        approve(args[0], args[1])
    elif command == "lint" and len(args) == 1:
        hits = lint(Path(args[0]).read_text(encoding="utf-8"))
        print("\n".join(hits))
        return 1 if hits else 0
    elif command == "unhandled" and not args:
        print(json.dumps(unhandled(json.load(sys.stdin)), indent=2))
    elif command == "handled" and args:
        mark_handled(args)
    elif command == "friction" and len(args) <= 1:
        print(friction(_report(args)))
    elif command == "records" and len(args) <= 1:
        print(records(_report(args)))
    elif command == "batch-add" and len(args) == 1:
        batch_add(args[0])
    elif command == "batches" and not args:
        print("\n".join(batches()))
    elif command == "batch-done" and len(args) == 1:
        batch_done(args[0])
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


def main(argv: list[str]) -> int:
    try:
        return _dispatch(argv[0] if argv else "", argv[1:])
    except (OSError, ValueError) as exc:  # ValueError covers bad JSON too
        print(f"orchestrator.py: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
