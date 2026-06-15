import signal
import socket
import subprocess
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

    # ─────────────────────────────────────────────
    # Determine PIPE usage
    # ─────────────────────────────────────────────
    stdout_mode = subprocess.PIPE if not quiet or capture_output or out else None
    stdin_mode = subprocess.PIPE if text_input is not None else None

    # ─────────────────────────────────────────────
    # Start process
    # ─────────────────────────────────────────────
    with subprocess.Popen(
        cmd,
        stdin=stdin_mode,
        stdout=stdout_mode,
        stderr=subprocess.STDOUT,
        text=True,
    ) as proc:
        assert proc.stdout is not None  # Invalid: Must have stdout

        # ─────────────────────────────────────────────
        # Input handling
        # ─────────────────────────────────────────────
        if text_input is not None:
            assert proc.stdin is not None  # Invalid: text_input without stdin=PIPE
            proc.stdin.write(text_input)
            proc.stdin.close()

        # ─────────────────────────────────────────────
        # Output handling
        # ─────────────────────────────────────────────
        output_chunks: list[str] = []

        while proc.poll() is None:
            line = proc.stdout.readline()
            if not quiet:
                print(line, end="")
            output_chunks.append(line)

        output = "".join(output_chunks)

    # ─────────────────────────────────────────────
    # File output behavior
    # ─────────────────────────────────────────────
    if out is not None:
        out.write_text(output)

    return output if capture_output else None


@contextmanager
def cr_workers(toml_path: Path, repo_root: Path, timeout: int = 10):
    """
    Context manager for Cosmic Ray HTTP workers via cr-http-workers.

    Responsibilities delegated entirely to cr-http-workers:
    - worker lifecycle
    - git cloning
    - HTTP server startup
    - cleanup on termination
    """

    def __wait_for_workers():
        """Wait for all worker URLs to be responsive before yielding control."""

        def __wait_for_worker(url: ParseResult):
            """Wait for a single worker URL to be responsive.

            Args:
                url (ParseResult): Worker URL in the form "host:port" (e.g. "localhost:8000")
            """
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                # Throw a socket.timeout error if any operation takes longer than 5 seconds
                sock.settimeout(timeout)
                try:
                    sock.connect((url.hostname, url.port))
                    print(f"Worker at {url.hostname}:{url.port} is responsive.")
                except socket.timeout:
                    print("The connection attempt timed out!")

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
            executor.map(__wait_for_worker, worker_urls)

    def __shutdown():
        """Shutdown the HTTP workers by terminating main process"""
        # TODO: This is some strange currying
        # `proc` comes from the outer scope of `cr_workers`
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    try:
        proc = subprocess.Popen(
            ["cr-http-workers", str(toml_path), str(repo_root)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

        __wait_for_workers()
        print("All workers are responsive...")
        yield proc
    finally:
        print("cr-http-workers shutting down...")
        __shutdown()


# ─────────────────────────────────────────────────────────────
# Main mutation pipeline
# ─────────────────────────────────────────────────────────────


def main() -> None:
    # ── Init session
    print(f"Init: {SESSION_NAME}")
    input("Press Enter to continue...")
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
                "--session-file",
                str(BASELINE_FILE),
                str(TOML_PATH),
            ]
        )

        # ── Exec Mutations ───────────────────────────────────────
        print(f"exec: {SESSION_NAME}")
        input("Press Enter to continue...")
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
    input("Press Enter to continue...")
    run(
        [
            "cr-report",
            "--surviving-only",
            str(SESSION_FILE),
        ]
    )

    print(f"cr-html: {SESSION_NAME}")
    input("Press Enter to continue...")
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
    input("Press Enter to continue...")
    dump = run(
        [
            "cosmic-ray",
            "dump",
            str(SESSION_FILE),
        ],
        capture_output=True,
        quiet=True,
    )

    print(f"jq: {SESSION_NAME}")
    input("Press Enter to continue...")
    run(
        [
            "jq",
            "-sn",
            "[inputs]",
        ],
        text_input=dump,
        out=REPORTS_DIR / "mutation.json",
        quiet=True,
    )


if __name__ == "__main__":
    main()
