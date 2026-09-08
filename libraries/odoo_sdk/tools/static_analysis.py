import subprocess
from pathlib import Path

ROOT = Path.cwd()
SRC = ROOT / "src" / "odoo_sdk"
REPORTS = ROOT / "reports"

# Trees `black` owns. The style itself comes from `[tool.black]` in
# pyproject.toml; this only decides what the formatter is pointed at.
FORMAT_PATHS = ["src", "tests", "tools"]


def run(cmd: list[str], out: Path | None = None, check: bool = True) -> int:
    with subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    ) as proc:
        lines = []
        for line in proc.stdout:
            print(line, end="", flush=True)
            lines.append(line)
        proc.wait()

    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd)

    if isinstance(out, Path):
        out.write_text("".join(lines))

    return proc.returncode


def check_formatting() -> None:
    """Fail the run when anything under ``FORMAT_PATHS`` is unformatted.

    ``black --check`` exits 1 when files would be reformatted and something
    else (123, 2) when black itself could not run. Both fail the gate, but
    they are different problems, so each gets its own message instead of the
    ``CalledProcessError`` traceback ``run`` would otherwise raise.
    """
    print(f"$ black --check {' '.join(FORMAT_PATHS)}", flush=True)
    returncode = run(["black", "--check", *FORMAT_PATHS], check=False)

    if returncode == 0:
        return

    if returncode == 1:
        raise SystemExit(
            "\nFormatting check failed: the files listed above are not "
            "black-formatted.\nFix them in place with:\n\n"
            "    make format\n"
        )

    raise SystemExit(
        f"\nFormatting check could not run: black exited {returncode}. "
        "This is a black failure, not a formatting failure.\n"
    )


def main():
    (REPORTS / "radon").mkdir(parents=True, exist_ok=True)
    (REPORTS / "complexipy").mkdir(parents=True, exist_ok=True)

    # Cheapest check first, and it keeps the complexipy verdict as the last
    # thing printed on a passing run.
    check_formatting()

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
