#!/usr/bin/env python3
"""Host-side helpers for /odoo-orchestrator:run.

    plan <task>                 create the next tasks/<task>/draft-N.md; print its path
    approve <task> <draft>      copy a draft onto tasks/<task>/brief.md; print its path
    approved <task>             print the brief path when brief.md exists, else nothing
    approve-job <job_id>...     stamp the brief of each submitted job as approved
    lint <brief>                a backstop for obvious breaches of the requirement line
    unhandled                   print the dead letters not yet handled, as JSON
    handled <job_id>...         record dead letters that are dealt with
    friction <batch_id>         record friction of the batch not yet recorded; print the count
    records <batch_id>          print the batch as a table
    batches                     print the finished batches not yet reported
    batch-done <batch_id>       mark every job of a batch reported

The state root is ``$ODOO_DEV_HOME`` (``odoo_dev_data.home.resolve_home``). Briefs
live under ``tasks/<task>/``; handled, friction and reported marks are columns of
the job row in ``state.db``. Nothing here writes a timesheet.

The linter is not the enforcement. The requirement line is semantic, and the
command text carries it.
"""

import json
import re
import sys
from pathlib import Path

from dispatcher import reporting
from odoo_dev_data.home import resolve_home
from odoo_dev_data.jobs import InvalidJobTransitionError, JobStore
from odoo_dev_data.migrate import LEGACY_BATCH
from odoo_dev_data.tasks import (
    append_friction,
    approve_draft,
    brief_path,
    next_draft_path,
)

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
BATCH_ID = re.compile(r"^[0-9a-f]{32}$")
#: The dispatcher refuses a larger prompt; flag it before the call does.
MAX_BRIEF_BYTES = 100_000

_EXT = (
    r"py|xml|csv|js|ts|tsx|jsx|scss|css|html|sql|json|yaml|yml|sh|conf|cfg|toml|md|txt"
)
_CODE_EXT = r"py|xml|js|ts|tsx|jsx|scss|sql|sh|yaml|yml|conf|toml"
_COMMANDS = (
    r"git|docker|devcontainer|odoo-bin|odoo|pytest|uv|pip|npm|bash|sh|make|psql|curl"
)

# Obvious cases only. A module, model or method name has no lexical marker, so
# a brief can pass every rule here and still prescribe the change.
LINT_RULES = (
    ("a code block", re.compile(r"^\s*(```|~~~)")),
    ("a shell prompt line", re.compile(r"^\s*\$\s+\S")),
    (
        "a file path",
        re.compile(rf"(?<![\w/:.-])(?:\.{{0,2}}/)?(?:[\w.-]+/)+[\w.-]+\.(?:{_EXT})\b"),
    ),
    # A bare name is flagged only for code-like extensions. md, csv, txt and json
    # stay exempt because they are business words ("totals.csv export"), and
    # node.js is a product name.
    (
        "a file name",
        re.compile(
            rf"(?<![\w/:.-])(?!node\.js\b)[\w-]+(?:\.[\w-]+)*\.(?:{_CODE_EXT})\b"
        ),
    ),
    ("a command", re.compile(rf"`(?:{_COMMANDS})\s[^`]*`")),
    (
        "a command",
        re.compile(r"\b(?:odoo-bin|docker compose|devcontainer up|pytest)\b"),
    ),
    # Upper case only: "select a customer from the list" is ordinary prose.
    (
        "SQL",
        re.compile(
            r"\b(?:SELECT\s.+\sFROM|INSERT\s+INTO|DELETE\s+FROM|UPDATE\s+\w+\s+SET)\b"
        ),
    ),
    ("an XPath", re.compile(r"//\w+\[|\bxpath\b", re.I)),
)


def _check_task(task: str) -> None:
    if not NAME.match(task):
        raise ValueError(f"task must be one plain name: {task!r}")


def _check_batch(batch_id: str) -> None:
    if batch_id != LEGACY_BATCH and not BATCH_ID.match(batch_id):
        raise ValueError(
            f"batch id must be 32 hex digits or {LEGACY_BATCH!r}: {batch_id!r}"
        )


def approved_brief(task: str) -> str:
    """The approved brief path of a task, or empty when brief.md is absent."""
    _check_task(task)
    path = brief_path(task, resolve_home())
    return str(path) if path.is_file() else ""


def lint(text: str) -> list[str]:
    hits = []
    if len(text.encode()) > MAX_BRIEF_BYTES:
        hits.append(
            f"the brief is over {MAX_BRIEF_BYTES} bytes; the dispatcher refuses it"
        )
    for number, line in enumerate(text.splitlines(), 1):
        for label, rule in LINT_RULES:
            if rule.search(line):
                hits.append(f"line {number}: {label}: {line.strip()[:100]}")
    return hits


def unhandled() -> list[dict]:
    """Dead letters with no ``reported_us``, each with its approved brief."""
    out = []
    for job in reporting.dead_letters(resolve_home()):
        if job.reported_us is None:
            entry = {**reporting.job_entry(job), "repo": job.repo, "round": job.round}
            task = str(job.task_id)
            out.append(
                {
                    **entry,
                    "approved_brief": approved_brief(task) if NAME.match(task) else "",
                }
            )
    return out


def mark_reported(job_ids: list[str]) -> None:
    with JobStore(home=resolve_home()) as store:
        store.mark_reported(job_ids)


def friction(batch_id: str) -> int:
    """Record friction of jobs not yet recorded; return how many entries."""
    _check_batch(batch_id)
    home, count = resolve_home(), 0
    with JobStore(home=home) as store:
        for job in reporting.batch_report(home, batch_id)["jobs"]:
            entries = (job["structured"] or {}).get("friction")
            if not entries or not isinstance(entries, list):
                continue
            if store.get(job["job_id"]).friction_recorded_us is not None:
                continue
            append_friction(job["task"], entries, job_id=job["job_id"], home=home)
            store.mark_friction_recorded([job["job_id"]])
            count += len(entries)
    return count


def records(batch_id: str) -> str:
    _check_batch(batch_id)
    head = "| task | job | state | outcome | seconds | questions | cost_usd | summary |"
    rows = [head, "|" + " --- |" * 8]
    for job in reporting.batch_report(resolve_home(), batch_id)["jobs"]:
        summary = (job["structured"] or {}).get("summary") or job["dead_reason"] or ""
        seconds = job["duration_s"]
        cells = [
            job["task"],
            job["job_id"],
            job["state"],
            job["outcome"],
            round(seconds) if seconds is not None else "",
            job["question_count"],
            job["cost_usd"],
            str(summary).replace("|", "/").replace("\n", " "),
        ]
        rows.append("| " + " | ".join(str(c) for c in cells) + " |")
    return "\n".join(rows)


def batch_done(batch_id: str) -> None:
    _check_batch(batch_id)
    home = resolve_home()
    mark_reported(
        [job["job_id"] for job in reporting.batch_report(home, batch_id)["jobs"]]
    )


def approve_jobs(job_ids: list[str]) -> None:
    with JobStore(home=resolve_home()) as store:
        for job_id in job_ids:
            store.approve_brief(job_id)


def _lint_file(path: str) -> int:
    hits = lint(Path(path).read_text(encoding="utf-8"))
    print("\n".join(hits))
    return 1 if hits else 0


def _plan(task: str) -> None:
    _check_task(task)
    print(next_draft_path(task, resolve_home()))


def _approve(task: str, draft: str) -> None:
    _check_task(task)
    print(approve_draft(task, Path(draft), resolve_home()))


#: verb -> (number of arguments, or None for one or more; handler). A handler
#: returns an exit code, or None for 0.
VERBS = {
    "plan": (1, _plan),
    "approve": (2, _approve),
    "approved": (1, lambda task: print(approved_brief(task))),
    "approve-job": (None, lambda *ids: approve_jobs(list(ids))),
    "lint": (1, _lint_file),
    "unhandled": (0, lambda: print(json.dumps(unhandled(), indent=2))),
    "handled": (None, lambda *ids: mark_reported(list(ids))),
    "friction": (1, lambda batch_id: print(friction(batch_id))),
    "records": (1, lambda batch_id: print(records(batch_id))),
    "batches": (
        0,
        lambda: print("\n".join(reporting.batches_pending_report(resolve_home()))),
    ),
    "batch-done": (1, batch_done),
}


def _dispatch(command: str, args: list[str]) -> int:
    arity, handler = VERBS.get(command, (-1, None))
    if handler is None or (len(args) != arity if arity is not None else not args):
        print(__doc__, file=sys.stderr)
        return 2
    return handler(*args) or 0


def main(argv: list[str]) -> int:
    try:
        return _dispatch(argv[0] if argv else "", argv[1:])
    except (OSError, ValueError, KeyError, InvalidJobTransitionError) as exc:
        print(f"orchestrator.py: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
