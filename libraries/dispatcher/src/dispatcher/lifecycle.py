"""Rendering and running the dispatch command line (design sections 8 and 12).

Every subprocess goes through :class:`Shell`, so tests replace it and nothing
here builds a container. Non-negotiable points, each with its reason:

* ``--config``, never ``--override-config``: the override makes the CLI resolve
  the config's relative compose paths as if it sat beside the repo, and the run
  dies on a missing file.
* ``COMPOSE_PROJECT_NAME`` is never set, and is removed from the environment of
  every child: each compose overlay opens with ``name: <repo>`` and an
  environment variable outranks that file, so setting it would fork a second
  stack.
* ``--workspace-folder`` decides whether the container holds the code or an
  empty directory. Docker answers a missing bind source by creating an empty
  root-owned directory, so the workspace is checked before ``up``
  (:func:`require_workspace`: it must be a git checkout) and the mounts are
  asserted after (:func:`assert_mounts`, paths only). The two together are
  needed: Docker reports the source path unchanged whether the directory holds
  the repo, is empty, or was just created for it. The dispatcher never creates
  the mount or the workspace.
* ``--append-system-prompt`` carries the invariants text, read on the host and
  inlined like the brief (#1083). A ``-file`` flag would need a path the
  container can open, and the plugin lives in the marketplace cache there. The
  flag was retired for the global ``claude`` wrapper (#740) because a flag
  reaches only the session the wrapper starts, never a forked subagent. The
  dispatcher is neither wrapper.
* Two independent idempotent lifecycle checks, not a state machine: ``up`` on
  dispatch, and stop when the project has nothing running and nothing pending.
  Stop, never remove, and never touch the shared sidecars.
"""

import json
import os
import re
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from odoo_sdk.state.dispatch_state import artifacts_root, odoo_dev_dir

from .schema import schema_argument
from .settings import (
    ARTIFACTS_TARGET,
    DEFAULT_ENTRY,
    ODOO_DEV_MOUNT_TARGET,
    ConfigResolutionError,
    Settings,
    WORKSPACE_TARGET,
    normalize_project,
)

#: ``CLAUDE_CONFIG_DIR`` inside the container (personal-features Feature).
#: A rule path starting with ``//`` is absolute, so it names this directory and
#: not a path under the project.
CONTAINER_CLAUDE_HOME = "/usr/local/share/claude-home"
#: Docker (accident prevention, design section 6), the configuration directory
#: (an unattended session must not edit the settings that govern it) and the two
#: task tracking tools (the run record, not the agent, measures the time).
#: ``render_exec`` passes each rule as its own ``--disallowedTools`` argument.
#: Bash redirects into the directory cannot be matched by a rule; the guard hook
#: covers those.
DISALLOWED_TOOLS = (
    "Bash(docker:*)",
    f"Edit(/{CONTAINER_CLAUDE_HOME}/**)",
    f"Write(/{CONTAINER_CLAUDE_HOME}/**)",
    "mcp__odoo-mcp__start_task",
    "mcp__odoo-mcp__stop_task",
)
UNATTENDED_VARIABLE = "ODOO_UNATTENDED"
STATE_DIR_VARIABLE = "ODOO_DEV_STATE_DIR"
JOB_ID_VARIABLE = "ODOO_JOB_ID"
#: Admission holds the single admission thread, and the per-job deadline does
#: not cover it, so every call made while admitting has its own timeout.
UP_TIMEOUT_S = 1200
#: The wait for postgres and odoo to answer inside a started container
#: (:func:`wait_ready`). It is under ``UP_TIMEOUT_S``: a cold start that is
#: still not answering after this long is a failed start, not a slow one.
READY_TIMEOUT_S = 180
READY_POLL_S = 2
#: One probe call. A hung ``docker exec`` must not eat the wait unseen.
READY_PROBE_TIMEOUT_S = 30
INSPECT_TIMEOUT_S = 60
STOP_TIMEOUT_S = 120


class StageError(RuntimeError):
    """A lifecycle stage failed; ``detail`` is everything the dispatcher saw."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class ContainerStartError(StageError):
    """CONTAINER_START_FAILED."""


class MountInvalidError(StageError):
    """WORKSPACE_MOUNT_INVALID."""


def resolve_config(settings: Settings, repo: str) -> Path:
    """``settings.config(repo)``, with a failure raised as a per-job stage error.

    ``JobRun.admit`` turns :class:`ContainerStartError` into a dead letter, so
    an unresolvable config ends one job and never the dispatcher.
    """
    try:
        return settings.config(repo)
    except ConfigResolutionError as exc:
        raise ContainerStartError(exc.detail) from exc


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class Shell:
    """The one place a subprocess is started. Replaced wholesale in tests."""

    def run(
        self, argv: list[str], *, env: dict[str, str], timeout: float | None = None
    ) -> Completed:
        try:
            done = subprocess.run(
                argv, env=env, capture_output=True, text=True, timeout=timeout
            )
        except OSError as exc:
            return Completed(127, "", str(exc))
        except subprocess.TimeoutExpired as exc:
            return Completed(124, "", f"timed out after {exc.timeout}s")
        return Completed(done.returncode, done.stdout, done.stderr)

    def spawn(self, argv: list[str], *, env: dict[str, str]) -> subprocess.Popen:
        # Own session and process group, so the whole host-side tree (the CLI
        # and anything holding its pipes) can be killed with one killpg.
        return subprocess.Popen(
            argv,
            env=env,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    def killpg(self, proc: subprocess.Popen, sig: int) -> None:
        """Signal the process group of a spawned child (its pgid is its pid)."""
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass


def child_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for every child process.

    ``COMPOSE_PROJECT_NAME`` is removed, never set.
    """
    env = dict(os.environ if base is None else base)
    env.pop("COMPOSE_PROJECT_NAME", None)
    return env


def render_up(settings: Settings, repo: str) -> list[str]:
    """``devcontainer up``: start or reuse the project's container.

    This deliberately does not call ``stack-ensure.sh``. Its create path runs
    ``devcontainer up --workspace-folder`` with no ``--config`` and no
    ``--mount``, so it cannot create a container for these repos (they carry no
    ``.devcontainer``). Do not "fix" this back. The memory gate stands in for
    eviction, and stopping an idle editor stack is a manual step.

    No ``--mount`` is passed. ``devcontainer up`` on a running container ignores
    it, and a container that VS Code made is the normal state. The Feature binds
    the host ``~/.config/odoo-dev`` at ``/usr/local/share/odoo-dev`` in every
    container, and the bundle root lives under that bind. One container serves
    several jobs of a project; each job points ``ODOO_DEV_STATE_DIR`` at its own
    subdirectory of the bundle root.
    """
    argv = [
        "devcontainer",
        "up",
        "--config",
        str(resolve_config(settings, repo)),
        "--workspace-folder",
        str(settings.workspace(repo)),
        "--additional-features",
        settings.additional_features,
    ]
    return argv


def render_exec_prefix(settings: Settings, repo: str) -> list[str]:
    """``devcontainer exec --config X --workspace-folder Y``, without the ``--``.

    The one place that names the project's container for ``exec``. The session
    command (:func:`render_exec`) and the reap (``reap.py``) both start from
    it, so the two cannot reach different containers. The caller adds its own
    options (``--remote-env``) and then ``--`` and the command.
    """
    return [
        "devcontainer",
        "exec",
        "--config",
        str(resolve_config(settings, repo)),
        "--workspace-folder",
        str(settings.workspace(repo)),
    ]


def job_entry(job, settings: Settings) -> str:
    """The job's own entry, else the dispatcher's ``settings.entry``."""
    return job.entry or settings.entry


def render_exec(
    settings: Settings,
    repo: str,
    task_id: str,
    job_id: str,
    brief: str,
    entry: str | None = None,
):
    """The dispatch command line of design section 8, exactly.

    ``--remote-env ODOO_UNATTENDED=1`` is a contract with the odoo-dev guard
    hook (``plugins/odoo-dev/hooks/unattended-guard.sh``). The stage commands
    fork, so inside ``/odoo-dev:pr`` the hook payload's ``agent_type`` is
    ``odoo-dev-pr``, not ``odoo-dev-implementer``, and an agent-type trigger
    allows the push, the pull request creation and ``gh pr ready`` that happen
    in that fork. The hook therefore also engages on any non-empty
    ``ODOO_UNATTENDED``, and a forked subagent inherits the session
    environment. Without this variable every protection in that hook is
    decorative during an unattended run. Do not drop it as redundant.

    The prompt starts with ``entry``, else ``settings.entry`` (``/odoo-dev:implement``), a
    forked command that names its own agent, so there is no ``--agent`` flag.
    A slash command in the prompt string expands before the run, and ``-p``
    waits for the fork. The task id is the command's first argument.
    """
    return [
        *render_exec_prefix(settings, repo),
        "--remote-env",
        f"{STATE_DIR_VARIABLE}={ARTIFACTS_TARGET}/{job_id}",
        "--remote-env",
        f"{UNATTENDED_VARIABLE}=1",
        "--remote-env",
        f"{JOB_ID_VARIABLE}={job_id}",
        "--",
        "claude",
        "-p",
        f"{entry or settings.entry} {task_id}\n\n{brief}",
        "--append-system-prompt",
        settings.invariants,
        "--output-format",
        "stream-json",
        "--verbose",
        "--json-schema",
        schema_argument(),
        "--permission-mode",
        "dontAsk",
        *[arg for rule in DISALLOWED_TOOLS for arg in ("--disallowedTools", rule)],
    ]


def _ere_literal(text: str) -> str:
    """``text`` as an extended-regex literal: only word characters, ``:``, ``/``, ``-`` stay bare."""
    return re.sub(r"[^\w:/-]", lambda found: "\\" + found.group(), text)


def kill_pattern(task_id: str, entry: str = DEFAULT_ENTRY) -> str:
    """Extended regex for ``pkill -f``: this task's ``claude -p`` command line.

    The line is ``claude -p <entry> <task> ...``, but the process the kernel
    shows is not always ``claude``: the container's ``claude`` is a wrapper
    (personal-features ``install.sh``) that replaces itself with the real
    binary, so the line can start with ``<path>/claude.exe``, or with
    ``node <path>/cli.js``. All of those match.

    Anchored with ``^``: the command line must *start* with one of them, so a
    sibling's inlined brief containing ``claude -p <entry> <task> `` further
    along never matches. The entry and the task id are literals, and the task
    id is followed by whitespace, so task 12 never matches task 123.
    """
    return (
        r"^(([^ ]*/)?node )?([^ ]*/)?(claude(\.exe)?|cli\.js) -p "
        rf"{_ere_literal(entry)} {_ere_literal(task_id)}[[:space:]]"
    )


def kill_in_container(
    shell: Shell,
    container_id: str,
    task_id: str,
    env: dict[str, str],
    entry: str = DEFAULT_ENTRY,
) -> str:
    """Best effort: stop the job's ``claude`` inside the container. Returns a note.

    Killing the host ``devcontainer exec`` process does not reach the process it
    started in the container. ``pkill -f`` matches the
    ``claude -p <entry> <task>`` command line, anchored at the start (see
    :func:`kill_pattern`) so a sibling job's argv that merely contains that text
    is never matched. Pass ``settings.entry``: the default is only right while
    ``DISPATCH_ENTRY`` is unset. Never verified, so the note says what is known.
    """
    if not container_id:
        return "no container id is known; the container-side process may still be alive"
    pattern = kill_pattern(task_id, entry)
    done = shell.run(
        ["docker", "exec", container_id, "pkill", "-KILL", "-f", "--", pattern],
        env=env,
        timeout=30,
    )
    if done.returncode == 0:
        return "a kill was sent to the container-side claude process; its exit was not verified"
    return (
        f"the container-side kill did not run (exit {done.returncode}: "
        f"{(done.stderr or done.stdout).strip()[:200]}); "
        "the container-side process may still be alive"
    )


def container_name(repo: str) -> str:
    return f"{normalize_project(repo)}-odoo-1"


def _container_id(result: Completed) -> str:
    """The container id from ``devcontainer up``'s last JSON stdout line."""
    for line in reversed(result.stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if data.get("outcome") == "success" and data.get("containerId"):
            return data["containerId"]
        raise ContainerStartError(
            f"devcontainer up reported {line[:500]}; exit={result.returncode}"
        )
    raise ContainerStartError(_failure_text("devcontainer up", result))


def _failure_text(what: str, result: Completed) -> str:
    return (
        f"{what} exit={result.returncode}: " + (result.stderr or result.stdout)[-1000:]
    )


def up(shell: Shell, settings: Settings, repo: str, env: dict[str, str]) -> str:
    """Start (or reuse) the project's container and return its id."""
    result = shell.run(render_up(settings, repo), env=env, timeout=UP_TIMEOUT_S)
    if result.returncode != 0:
        raise ContainerStartError(_failure_text("devcontainer up", result))
    return _container_id(result)


def _probe(
    shell: Shell, container_id: str, argv: list[str], env: dict[str, str]
) -> tuple[bool, str]:
    """Run one readiness probe inside the container. Returns (passed, text)."""
    done = shell.run(
        ["docker", "exec", container_id, *argv],
        env=env,
        timeout=READY_PROBE_TIMEOUT_S,
    )
    name = " ".join(argv)
    if done.returncode == 0:
        return True, f"{name} ok"
    return False, _failure_text(name, done)


def wait_ready(
    shell: Shell,
    container_id: str,
    env: dict[str, str],
    *,
    timeout_s: float | None = None,
    poll_s: float | None = None,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
) -> None:
    """Wait until postgres and odoo answer inside the started container.

    ``devcontainer up`` returns when the container is running, not when its
    services answer. The probes are the ones ``stack-ensure.sh`` uses
    (``pg_isready`` and ``odoo --version``), run through ``docker exec`` as
    :func:`kill_in_container` does. Both run on every attempt so that an
    expired wait reports both. An expired wait raises ``ContainerStartError``
    with the last output of each probe. The defaults are read at call time.
    """
    timeout_s = READY_TIMEOUT_S if timeout_s is None else timeout_s
    poll_s = READY_POLL_S if poll_s is None else poll_s
    sleep = time.sleep if sleep is None else sleep
    clock = time.monotonic if clock is None else clock
    deadline = clock() + timeout_s
    while True:
        pg_ok, pg_text = _probe(shell, container_id, ["pg_isready"], env)
        odoo_ok, odoo_text = _probe(shell, container_id, ["odoo", "--version"], env)
        if pg_ok and odoo_ok:
            return
        if clock() >= deadline:
            raise ContainerStartError(
                f"readiness: not ready after {timeout_s}s: {pg_text} / {odoo_text}"
            )
        sleep(poll_s)


def _bind_sources(mounts: list[dict]) -> dict[str, str]:
    return {
        str(mount.get("Destination", "")): os.path.realpath(mount.get("Source", ""))
        for mount in mounts
        if mount.get("Type") == "bind"
    }


def _is_git_checkout(dot_git: Path) -> bool:
    if dot_git.is_dir():
        return (dot_git / "HEAD").is_file()
    if dot_git.is_file():
        return dot_git.read_text(errors="replace").startswith("gitdir:")
    return False


def require_workspace(settings: Settings, repo: str) -> None:
    """Refuse to start unless ``<repos>/<repo>`` is a git checkout.

    A missing or empty directory would pass :func:`assert_mounts` (the bind
    source path is identical) and the session would run in an empty tree and be
    billed. ``.git`` is either a directory holding ``HEAD`` or a file (a
    worktree) that begins with ``gitdir:``; an empty or junk ``.git`` is refused.

    Known: an unreadable workspace or parent directory makes ``Path.exists``
    raise ``PermissionError`` (Python 3.13). That is not a MountInvalidError, so
    ``JobRun.admit`` dead-letters the job as CONTAINER_START_FAILED and
    re-raises, which aborts ``run()`` and leaves other jobs PENDING until the
    next run. Rare, and loud (exit 1), so it is left as it is.
    """
    workspace = settings.workspace(repo)
    if not _is_git_checkout(workspace / ".git"):
        raise MountInvalidError(
            f"{workspace} is missing, empty or not a git checkout (no .git); "
            "docker would bind an empty directory and the session would run in it"
        )


def assert_mounts(
    shell: Shell, settings: Settings, repo: str, container_id: str, env: dict[str, str]
) -> None:
    """Assert, after the container is up, that the binds are the ones we asked for.

    The bind at ``/mnt/extra-addons`` (where the config declares the workspace
    and where ``reap`` assumes it) must have the real ``--workspace-folder`` as
    its source, by exact equality so a parent-directory bind is rejected too.
    The ``/usr/local/share/odoo-dev`` bind (the Feature's) must have the host
    odoo-dev directory as its source, and the bundle root must lie under it.
    Anything else, or no bind at all, is wrong. This checks paths only and cannot see whether the
    source holds the repo; :func:`require_workspace` covers that before ``up``.
    Asserted, never created.
    """
    result = shell.run(
        ["docker", "inspect", "--format", "{{json .Mounts}}", container_id],
        env=env,
        timeout=INSPECT_TIMEOUT_S,
    )
    if result.returncode != 0:
        raise MountInvalidError(_failure_text("docker inspect", result))
    try:
        mounts = json.loads(result.stdout)
    except ValueError as exc:
        raise MountInvalidError(f"docker inspect output is not JSON: {exc}")
    binds = _bind_sources(mounts or [])
    wanted_workspace = os.path.realpath(settings.workspace(repo))
    wanted_odoo_dev = os.path.realpath(odoo_dev_dir())
    problems = []
    if binds.get(WORKSPACE_TARGET) != wanted_workspace:
        problems.append(
            f"{WORKSPACE_TARGET} is not a bind of {wanted_workspace} "
            f"(the --workspace-folder; found {binds.get(WORKSPACE_TARGET)!r})"
        )
    if binds.get(ODOO_DEV_MOUNT_TARGET) != wanted_odoo_dev:
        problems.append(
            f"{ODOO_DEV_MOUNT_TARGET} is not a bind of {wanted_odoo_dev} "
            f"(found {binds.get(ODOO_DEV_MOUNT_TARGET)!r}); the container was "
            "created without the personal-features Feature: stop it and let "
            "the dispatcher create it"
        )
    elif not Path(os.path.realpath(artifacts_root())).is_relative_to(wanted_odoo_dev):
        problems.append(
            f"the bundle root {artifacts_root()} is not under {wanted_odoo_dev}, "
            f"so the container cannot see it at {ARTIFACTS_TARGET}"
        )
    if problems:
        raise MountInvalidError("; ".join(problems) + f"; binds={binds}")


def stop_project(shell: Shell, repo: str, env: dict[str, str]) -> bool:
    """Stop (never remove) the project's own compose project. True when stopped.

    Scoped to the project's compose project, so the shared sidecars (postgres,
    the memory service) belong to the whole machine and are never touched.
    Idempotent and best effort: a failure here never fails a job.
    """
    project = normalize_project(repo)
    done = shell.run(
        ["docker", "compose", "-p", project, "stop"], env=env, timeout=STOP_TIMEOUT_S
    )
    if done.returncode == 0:
        return True
    fallback = shell.run(
        ["docker", "stop", container_name(repo)], env=env, timeout=STOP_TIMEOUT_S
    )
    return fallback.returncode == 0


def stop_if_idle(shell: Shell, store, repo: str, env: dict[str, str]) -> bool:
    """Stop the project's container when it has no job running and none pending.

    Both halves, or a finishing job would stop a container out from under a
    sibling that is still mid-test. The check races (a job can arrive a second
    later and start it again); that costs one restart, which is why this is not
    guarded by a lock.
    """
    if store.count_live(repo) > 0:
        return False
    return stop_project(shell, repo, env)


def transcript_path(settings: Settings, session_id: str) -> Path | None:
    """Find the session transcript on the host, or None.

    The host config directory is mounted into the container on purpose, so the
    transcript is already on the host when the session ends.
    """
    if not session_id:
        return None
    for path in sorted(
        (settings.claude_home / "projects").glob(f"*/{session_id}.jsonl")
    ):
        return path
    return None
