#!/usr/bin/env python3
"""Enforce the mutation kill-rate floor and assemble CI drift artifacts.

CI-only helper for the mutation-testing gate (see
``.github/workflows/mutation.yaml``). It is intentionally self-contained: it
depends only on the Python stdlib and must NOT import the ``odoo_sdk`` package,
so it runs without the SDK being installed.

It reads the cosmic-ray ``mutation.json`` export (a list of mutant records,
each with a ``test_outcome``) and emits, on stdout, ``KEY=VALUE`` lines that a
GitHub Actions step can append to ``$GITHUB_OUTPUT``:

    kill_rate=<float, 1 decimal>
    passed=<true|false>
    survived=<int>
    total=<int>

Exit code is 0 when the floor is met and 1 when it is not, so a workflow step
can gate on either the exit status or the ``passed`` output.

With ``--survivors`` it instead prints the surviving mutants as a Markdown list
(for a drift-issue body) and always exits 0.

The 90% floor is a CI policy value; override it with ``KILL_RATE_FLOOR``.

Usage:
    python .github/scripts/mutation_gate.py <mutation.json>
    python .github/scripts/mutation_gate.py --survivors <mutation.json>
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# CI policy: the minimum acceptable mutation kill rate (percent). Kept here as
# the single CI-side source of truth; override via the KILL_RATE_FLOOR env var.
KILL_RATE_FLOOR = float(os.environ.get("KILL_RATE_FLOOR", "90.0"))

SURVIVOR_LIMIT = 50


def load_mutants(path: Path) -> list[dict]:
    """Load the cosmic-ray ``mutation.json`` export as a list of records."""
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError(f"expected a JSON list in {path}, got {type(data).__name__}")
    return data


def compute_kill_rate(mutants: list[dict]) -> float:
    """Kill rate (percent) over *completed* mutants in a cosmic-ray dump.

    Only mutants that ran (``test_outcome is not None``) count toward the rate;
    a mutant is killed iff its outcome is ``"killed"``. Returns ``0.0`` when
    nothing has completed.
    """
    completed = [m for m in mutants if m.get("test_outcome") is not None]
    if not completed:
        return 0.0
    killed = [m for m in completed if m.get("test_outcome") == "killed"]
    return len(killed) / len(completed) * 100


def surviving_mutants(mutants: list[dict]) -> list[dict]:
    """Completed mutants whose test outcome is not ``killed``."""
    completed = [m for m in mutants if m.get("test_outcome") is not None]
    return [m for m in completed if m.get("test_outcome") != "killed"]


def format_survivors(survivors: list[dict], limit: int = SURVIVOR_LIMIT) -> str:
    """Render surviving mutants as a Markdown list for an issue body."""
    if not survivors:
        return "_No surviving mutants._"
    lines = []
    for m in survivors[:limit]:
        module = m.get("module", "?")
        operator = m.get("operator", "?")
        occurrence = m.get("occurrence", "?")
        lines.append(f"- `{module}` — `{operator}` (occurrence {occurrence})")
    if len(survivors) > limit:
        lines.append(f"- …and {len(survivors) - limit} more")
    return "\n".join(lines)


def evaluate(path: Path, floor: float = KILL_RATE_FLOOR) -> dict:
    """Compute the gate result for a ``mutation.json`` export."""
    mutants = load_mutants(path)
    # Decide pass/fail on the same rounded value we report, so the published
    # status can never contradict the displayed rate (e.g. "90.0% but failed").
    kill_rate = round(compute_kill_rate(mutants), 1)
    survivors = surviving_mutants(mutants)
    return {
        "kill_rate": kill_rate,
        "passed": kill_rate >= floor,
        "survived": len(survivors),
        "total": len(mutants),
        "survivors": survivors,
    }


def main(argv: list[str]) -> int:
    args = argv[1:]
    survivors_only = False
    if args and args[0] == "--survivors":
        survivors_only = True
        args = args[1:]

    if len(args) != 1:
        print(
            "usage: mutation_gate.py [--survivors] <mutation.json>", file=sys.stderr
        )
        return 2

    result = evaluate(Path(args[0]))
    if survivors_only:
        print(format_survivors(result["survivors"]))
        return 0

    print(f"kill_rate={result['kill_rate']}")
    print(f"passed={'true' if result['passed'] else 'false'}")
    print(f"survived={result['survived']}")
    print(f"total={result['total']}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
