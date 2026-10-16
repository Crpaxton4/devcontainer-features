#!/usr/bin/env python3
"""Host-side helpers for /odoo-orchestrator:run. Standard library only.

    plan-path <task> <round>   print (and create the folder for) the brief path
    lint <brief>               flag text that breaks the requirement line
    unhandled                  stdin: dead-letters JSON; print the unread ones
    handled <job_id>...        record dead letters the user has dealt with
    friction                   stdin: run JSON; append friction, print the count
    records                    stdin: run JSON; print the batch as a table

The home directory is ``$ODOO_ORCHESTRATOR_HOME`` or
``~/.local/state/odoo-orchestrator``. It holds ``briefs/<task>/round-<n>.md``,
``handled.txt`` and ``friction/<date>.md``. Nothing here writes a timesheet.
"""

import json
import os
import re
import sys
from datetime import date
from pathlib import Path

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# The host owns what and why. A brief that names a file, a command or a build
# step has crossed the line, so each of these is a reason to rewrite it.
LINT_RULES = (
    ("a code block", re.compile(r"^\s*(```|~~~)")),
    ("a shell prompt line", re.compile(r"^\s*[$#]\s+\S")),
    (
        "a file path",
        re.compile(
            r"(?<![\w/])(?:\.{0,2}/)?[\w.-]+(?:/[\w.-]+)*\.(?:py|xml|csv|js|ts|scss|"
            r"json|yaml|yml|sh|conf|cfg|toml|md)\b"
        ),
    ),
    (
        "a command",
        re.compile(
            r"`(?:git|docker|devcontainer|odoo-bin|pytest|uv|pip|npm|bash|sh|make|"
            r"psql|curl)\s[^`]*`"
        ),
    ),
)


def home() -> Path:
    override = os.environ.get("ODOO_ORCHESTRATOR_HOME")
    return Path(override) if override else Path.home() / ".local/state/odoo-orchestrator"


def plan_path(task: str, rnd: int) -> Path:
    if not NAME.match(task) or rnd < 1:
        raise ValueError(f"task must be one plain name and round >= 1: {task!r} {rnd}")
    folder = home() / "briefs" / task
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"round-{rnd}.md"


def lint(text: str) -> list[str]:
    hits = []
    for number, line in enumerate(text.splitlines(), 1):
        for label, rule in LINT_RULES:
            if rule.search(line):
                hits.append(f"line {number}: {label}: {line.strip()[:100]}")
    return hits


def _handled() -> set[str]:
    try:
        return set((home() / "handled.txt").read_text().split())
    except OSError:
        return set()


def latest_brief(task: str) -> str:
    """The newest kept brief for a task, or empty when none is on disk."""
    folder = home() / "briefs" / task
    rounds = sorted(
        folder.glob("round-*.md"),
        key=lambda p: int(p.stem.split("-")[1]) if p.stem.split("-")[1].isdigit() else 0,
    )
    return str(rounds[-1]) if rounds else ""


def unhandled(letters: list[dict]) -> list[dict]:
    seen = _handled()
    out = []
    for letter in letters:
        if letter.get("job_id") in seen:
            continue
        task = str(letter.get("task", ""))
        brief = latest_brief(task) if NAME.match(task) else ""
        out.append({**letter, "kept_brief": brief})
    return out


def mark_handled(job_ids: list[str]) -> None:
    home().mkdir(parents=True, exist_ok=True)
    with (home() / "handled.txt").open("a") as handle:
        handle.write("".join(f"{job_id}\n" for job_id in job_ids))


def _envelope(job: dict) -> dict:
    bundle = job.get("bundle") or ""
    try:
        data = json.loads((Path(bundle) / "envelope.json").read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _structured(job: dict) -> dict:
    value = _envelope(job).get("structured_output")
    return value if isinstance(value, dict) else {}


def friction(report: dict, today: date | None = None) -> int:
    """Append every job's friction entries to today's file; return the count."""
    lines, count = [], 0
    for job in report.get("jobs", []):
        for entry in _structured(job).get("friction") or []:
            count += 1
            lines += [
                f"## task {job.get('task')} job {job.get('job_id')}",
                f"- tool: {entry.get('tool', '')}",
                f"- expected: {entry.get('expected', '')}",
                f"- observed: {entry.get('observed', '')}",
                f"- did: {entry.get('did', '')}",
                "",
            ]
    if lines:
        path = home() / "friction" / f"{(today or date.today()).isoformat()}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            handle.write("\n".join(lines) + "\n")
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


def main(argv: list[str]) -> int:
    command, args = (argv[0] if argv else ""), argv[1:]
    if command == "plan-path" and len(args) == 2 and args[1].isdigit():
        print(plan_path(args[0], int(args[1])))
    elif command == "lint" and len(args) == 1:
        hits = lint(Path(args[0]).read_text(encoding="utf-8"))
        print("\n".join(hits))
        return 1 if hits else 0
    elif command == "unhandled" and not args:
        print(json.dumps(unhandled(json.load(sys.stdin)), indent=2))
    elif command == "handled" and args:
        mark_handled(args)
    elif command == "friction" and not args:
        print(friction(json.load(sys.stdin)))
    elif command == "records" and not args:
        print(records(json.load(sys.stdin)))
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
