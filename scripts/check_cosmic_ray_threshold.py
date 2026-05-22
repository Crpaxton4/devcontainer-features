from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print(
            "Usage: check_cosmic_ray_threshold.py SESSION_FILE MIN_KILL_RATE",
            file=sys.stderr,
        )
        return 2

    session_file = Path(sys.argv[1])
    min_kill_rate = float(sys.argv[2])

    if not session_file.exists():
        print(f"Cosmic Ray session file does not exist: {session_file}", file=sys.stderr)
        return 2

    completed = 0
    killed = 0
    pending = 0
    outcomes: Counter[str] = Counter()

    dump = subprocess.run(
        ["uv", "run", "cosmic-ray", "dump", str(session_file)],
        check=True,
        capture_output=True,
        text=True,
    )

    for line in dump.stdout.splitlines():
        if not line.strip():
            continue
        _, result = json.loads(line)
        if result is None:
            pending += 1
            continue

        completed += 1
        outcome = result.get("test_outcome") or "unknown"
        outcomes[outcome] += 1
        if outcome == "killed":
            killed += 1

    if pending:
        print(
            f"Mutation session incomplete: {pending} work items still pending",
            file=sys.stderr,
        )
        return 1

    if completed == 0:
        print("No completed mutation results were found", file=sys.stderr)
        return 1

    kill_rate = (killed / completed) * 100
    summary = ", ".join(
        f"{name}={outcomes[name]}" for name in sorted(outcomes) if outcomes[name]
    )
    print(
        f"Cosmic Ray kill rate: {kill_rate:.2f}% "
        f"({killed}/{completed}; {summary or 'no outcomes'})"
    )

    if kill_rate < min_kill_rate:
        print(
            f"Kill rate {kill_rate:.2f}% is below required {min_kill_rate:.2f}%",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())