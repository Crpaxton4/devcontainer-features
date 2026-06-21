import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COVERAGE_REPORT_DIR = ROOT / "reports" / "coverage"


def main():
    subprocess.run(
        [
            "coverage", "run", "-m", "unittest",
            "discover", "-s", "test/odoo_sdk", "-p", "test_*.py", "-t", ".",
        ],
        check=True,
    )
    subprocess.run(["coverage", "report"], check=True)
    COVERAGE_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["coverage", "json", "-o", str(COVERAGE_REPORT_DIR / "coverage.json")],
        check=True,
    )


if __name__ == "__main__":
    main()
