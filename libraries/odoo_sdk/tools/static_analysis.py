import re
import subprocess
from pathlib import Path

ROOT = Path.cwd()
SRC = ROOT / "src" / "odoo_sdk"
REPORTS = ROOT / "reports"

# Internal ``from odoo_sdk import ...`` (or bare ``import odoo_sdk``)
# root-imports re-enter the partially initialized package during import and
# are how the transport/state error cycle formed. ADR-005 bans them inside
# ``src/odoo_sdk``; the shared-kernel façade ``odoo_sdk.errors`` (or the
# canonical module) is the sanctioned spelling. The package façade itself is
# the only exemption.
ROOT_IMPORT_PATTERN = re.compile(
    r"^\s*(?:from odoo_sdk import\b|import odoo_sdk\s*(?:$|as\b))",
    re.MULTILINE,
)
ROOT_IMPORT_EXEMPT = frozenset({SRC / "__init__.py"})

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


def check_layering() -> None:
    """Run the ADR-005 import-linter layering contracts.

    The contracts (and their frozen exception baseline) live under
    ``[tool.importlinter]`` in pyproject.toml; a broken contract or an
    unmatched ignore entry fails the gate.
    """
    print("$ lint-imports", flush=True)
    run(["lint-imports"])


def check_no_internal_root_imports() -> None:
    """Fail when a module inside ``src/odoo_sdk`` imports the package root.

    Grep-style companion to the import-linter contracts (ADR-005 rule 5):
    grimp records these edges as plain intra-package imports, so the cycle
    they create through ``odoo_sdk/__init__`` needs its own check.
    """
    print("$ static_analysis.py: no internal root-imports", flush=True)
    offenders = [
        path
        for path in sorted(SRC.rglob("*.py"))
        if path not in ROOT_IMPORT_EXEMPT
        and ROOT_IMPORT_PATTERN.search(path.read_text(encoding="utf-8"))
    ]

    if offenders:
        listing = "\n".join(f"    {path.relative_to(ROOT)}" for path in offenders)
        raise SystemExit(
            "\nInternal root-import check failed. These modules import the "
            "odoo_sdk package root:\n\n"
            f"{listing}\n\n"
            "Import from odoo_sdk.errors (or the canonical defining module) "
            "instead; see ADR-005.\n"
        )


def main():
    (REPORTS / "radon").mkdir(parents=True, exist_ok=True)
    (REPORTS / "complexipy").mkdir(parents=True, exist_ok=True)

    # Cheapest checks first, and it keeps the complexipy verdict as the last
    # thing printed on a passing run.
    check_formatting()
    check_no_internal_root_imports()
    check_layering()

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
