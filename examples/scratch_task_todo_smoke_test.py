"""Repository-local entrypoint for the task/todo smoke test.

This wrapper keeps the human-readable example in `examples/general/` while
supporting direct execution from the repository root with the shorter command
path used during local smoke checks.
"""

from __future__ import annotations

import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


if str(REPOSITORY_ROOT) not in sys.path:
    # Examples are often run from a source checkout before the package is
    # installed, so the repository root must be importable explicitly.
    sys.path.insert(0, str(REPOSITORY_ROOT))


from general.scratch_task_todo_smoke_test import main


if __name__ == "__main__":
    main()