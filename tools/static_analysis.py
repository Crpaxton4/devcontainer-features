from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path.cwd()
SRC = ROOT / "src" / "odoo_sdk"
REPORTS = ROOT / "reports" / "static"


def run(cmd: list[str], out: Path | None = None):
    result = subprocess.run(
        cmd,
        text=True,
        capture_output=(out is not None),
        check=True,
    )

    if isinstance(out, Path):
        out.write_text(result.stdout)


def main():
    (REPORTS / "radon").mkdir(parents=True, exist_ok=True)
    (REPORTS / "complexipy").mkdir(parents=True, exist_ok=True)

    run(
        ["radon", "cc", str(SRC), "--show-complexity", "--average", "--json"],
        REPORTS / "radon/cc.json",
    )

    run(
        ["radon", "raw", str(SRC), "--summary", "--json"],
        REPORTS / "radon/raw.json",
    )

    run(
        ["radon", "mi", str(SRC), "--show", "--json"],
        REPORTS / "radon/mi.json",
    )

    run(
        ["radon", "hal", str(SRC), "--json"],
        REPORTS / "radon/hal.json",
    )

    run(
        [
            "complexipy",
            "src",
            "--output-format",
            "json",
            "--output",
            str(REPORTS / "complexipy"),
            "--sort",
            "desc",
        ]
    )


if __name__ == "__main__":
    main()
