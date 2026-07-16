import json
import signal
import socket
import subprocess
import sys
import tomllib
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import ParseResult, urlparse

ROOT = Path(__file__).resolve().parents[1]
TOML_PATH = ROOT / "cosmic-ray.toml"

COSMIC_DIR = ROOT / ".cosmic-ray"
REPORTS_DIR = ROOT / "reports" / "mutation"

COSMIC_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

SESSION_NAME = f"session_{uuid.uuid4().hex[:12]}"
SESSION_FILE = COSMIC_DIR / f"{SESSION_NAME}.sqlite"
BASELINE_FILE = COSMIC_DIR / f"{SESSION_NAME}-baseline.sqlite"


def run(
    cmd: list[str],
    text_input: str | None = None,
    capture_output: bool = False,
    out: Path | None = None,
    quiet: bool = False,
) -> str | None:
    """
    Unified subprocess runner (Popen-only implementation).

    Behaviors:
    - default: run, fail fast, no return
    - capture_output: return stdout
    - out: write stdout to file
    """

    stdout_mode = subprocess.PIPE if not quiet or capture_output or out else None
    stdin_mode = subprocess.PIPE if text_input is not None else None

    with subprocess.Popen(
        cmd,
        stdin=stdin_mode,
        stdout=stdout_mode,
        stderr=subprocess.STDOUT,
        text=True,
    ) as proc:
        if text_input is not None:
            assert proc.stdin is not None  # Invalid: text_input without stdin=PIPE
            proc.stdin.write(text_input)
            proc.stdin.close()

        output_chunks: list[str] = []
        if proc.stdout is not None:
            for line in proc.stdout:
                if not quiet:
                    print(line, end="", flush=True)
                output_chunks.append(line)

        output = "".join(output_chunks)

    # A failed step (e.g. a broken ``cosmic-ray`` run) must raise rather than be
    # silently ignored, otherwise the pipeline can emit an empty/partial report
    # that looks clean. Mirrors ``tools/static_analysis.py``.
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, output)

    if out is not None:
        out.write_text(output)

    return output if capture_output else None


def _wait_for_worker(url: ParseResult, timeout: int) -> None:
    """Wait for a single worker URL to become responsive (or time out).

    :param url: Worker URL in the form "host:port" (e.g. "localhost:8000").
    :param timeout: Seconds any socket operation may take before timing out.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((url.hostname, url.port))
            print(f"Worker at {url.hostname}:{url.port} is responsive.")
        except socket.timeout:
            print("The connection attempt timed out!")


def _wait_for_workers(toml_path: Path, timeout: int) -> None:
    """Block until every worker URL configured in ``toml_path`` is responsive."""
    worker_urls = [
        urlparse(str(url))
        for url in (
            tomllib.loads(toml_path.read_text())
            .get("cosmic-ray", {})
            .get("distributor", {})
            .get("http", {})
            .get("worker-urls", [])
        )
    ]
    with ThreadPoolExecutor(max_workers=len(worker_urls)) as executor:
        print("Waiting for workers to be responsive...")
        print(worker_urls)
        executor.map(lambda url: _wait_for_worker(url, timeout), worker_urls)


def _shutdown_workers(proc: "subprocess.Popen[str]") -> None:
    """Shut the HTTP workers down by terminating (then killing) their process."""
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@contextmanager
def cr_workers(toml_path: Path, repo_root: Path, timeout: int = 10):
    """Context manager for Cosmic Ray HTTP workers via cr-http-workers.

    Responsibilities delegated entirely to cr-http-workers:
    - worker lifecycle
    - git cloning
    - HTTP server startup
    - cleanup on termination
    """
    proc = subprocess.Popen(
        ["cr-http-workers", str(toml_path), str(repo_root)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_workers(toml_path, timeout)
        print("All workers are responsive...")
        yield proc
    finally:
        print("cr-http-workers shutting down...")
        _shutdown_workers(proc)


# ─────────────────────────────────────────────────────────────
# Main mutation pipeline
# ─────────────────────────────────────────────────────────────


def _prompt(message: str) -> None:
    """Prompt user to continue, or skip if stdin is not interactive."""
    if not sys.stdin.isatty():
        return
    try:
        input(message)
    except EOFError:
        pass


def main() -> None:
    # ── Init session
    print(f"Init: {SESSION_NAME}")
    _prompt("Press Enter to continue...")
    run(
        [
            "cosmic-ray",
            "init",
            str(TOML_PATH),
            str(SESSION_FILE),
        ]
    )

    # ── Execute with HTTP workers ───────────────────────────────────
    with cr_workers(TOML_PATH, ROOT):
        # ── Baseline ─────────────────────────────────────────────
        print(f"Baseline: {SESSION_NAME}")
        run(
            [
                "cosmic-ray",
                "baseline",
                str(TOML_PATH),
            ]
        )

        # ── Exec Mutations ───────────────────────────────────────
        print(f"exec: {SESSION_NAME}")
        _prompt("Press Enter to continue...")
        run(
            [
                "cosmic-ray",
                "exec",
                str(TOML_PATH),
                str(SESSION_FILE),
            ]
        )

    # ── Report generation ────────────────────────────────────
    print(f"cr-report: {SESSION_NAME}")
    _prompt("Press Enter to continue...")
    run(
        [
            "cr-report",
            "--surviving-only",
            str(SESSION_FILE),
        ]
    )

    print(f"cr-html: {SESSION_NAME}")
    _prompt("Press Enter to continue...")
    run(
        [
            "cr-html",
            str(SESSION_FILE),
        ],
        out=REPORTS_DIR / "report.html",
        quiet=True,
    )

    # ── JSON export ──────────────────────────────────────────
    print(f"dump: {SESSION_NAME}")
    _prompt("Press Enter to continue...")
    dump = run(
        [
            "cosmic-ray",
            "dump",
            str(SESSION_FILE),
        ],
        capture_output=True,
        quiet=True,
    )

    # ``cosmic-ray dump`` emits NDJSON (one mutation record per line); wrap it in
    # a JSON array for the report tooling. Done in-process (no external ``jq``).
    records = [json.loads(line) for line in (dump or "").splitlines() if line.strip()]
    (REPORTS_DIR / "mutation.json").write_text(json.dumps(records))


if __name__ == "__main__":
    main()
