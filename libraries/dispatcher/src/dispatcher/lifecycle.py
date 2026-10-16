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
* ``--append-system-prompt-file`` is required here. It was retired for the global
  ``claude`` wrapper (#740) because a flag reaches only the session the wrapper
  starts, never a forked subagent. The dispatcher is neither wrapper.
* Two independent idempotent lifecycle checks, not a state machine: ``up`` on
  dispatch, and stop when the project has nothing running and nothing pending.
  Stop, never remove, and never touch the shared sidecars.
"""

import json
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

from odoo_sdk.state.dispatch_state import artifacts_root

from .schema import schema_argument
from .settings import (
    ARTIFACTS_TARGET,
    Settings,
    WORKSPACE_TARGET,
    normalize_project,
)

#: Docker (accident prevention, design section 6) and the configuration
#: directory: an unattended session must not edit the settings that govern it.
#: Space separated, as ``--disallowedTools`` takes one rule list. Bash redirects
#: into the directory cannot be matched by a rule; the guard hook covers those.
DISALLOWED_TOOLS = "Bash(docker:*) Edit(~/.claude/**) Write(~/.claude/**)"
UNATTENDED_VARIABLE = "ODOO_UNATTENDED"
STATE_DIR_VARIABLE = "ODOO_DEV_STATE_DIR"
ACTIVE_REPOS_VARIABLE = "ODOO_ACTIVE_REPOS"
#: Admission holds the single admission thread, and the per-job deadline does
#: not cover it, so every call made while admitting has its own timeout.
UP_TIMEOUT_S = 1200
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


def child_env(
    active_repos: list[str], base: dict[str, str] | None = None
) -> dict[str, str]:
    """Environment for every child process.

    ``COMPOSE_PROJECT_NAME`` is removed, never set. ``ODOO_ACTIVE_REPOS`` names
    the queue's in-flight projects so ``stack-ensure.sh``'s eviction sweep
    respects them.
    """
    env = dict(os.environ if base is None else base)
    env.pop("COMPOSE_PROJECT_NAME", None)
    env[ACTIVE_REPOS_VARIABLE] = ",".join(active_repos)
    return env


def render_up(settings: Settings, repo: str) -> list[str]:
    """``devcontainer up``: start or reuse the project's container.

    This deliberately does not call ``stack-ensure.sh``. Its create path runs
    ``devcontainer up --workspace-folder`` with no ``--config`` and no
    ``--mount``, so it cannot create a container for these repos (they carry no
    ``.devcontainer``) and could not add the ``/artifacts`` bind. Do not "fix"
    this back. Instead the dispatcher exports ``ODOO_ACTIVE_REPOS`` to every
    child (see :func:`child_env`) and the memory gate stands in for eviction.

    The bundle *root* is mounted at ``/artifacts`` because one container serves
    several jobs of a project; each job points ``ODOO_DEV_STATE_DIR`` at its own
    subdirectory.
    """
    argv = [
        "devcontainer",
        "up",
        "--config",
        str(settings.config(repo)),
        "--workspace-folder",
        str(settings.workspace(repo)),
        "--mount",
        f"type=bind,source={artifacts_root()},target={ARTIFACTS_TARGET}",
    ]
    if settings.additional_features:
        argv += ["--additional-features", settings.additional_features]
    return argv


def render_exec(settings: Settings, repo: str, task_id: str, job_id: str, brief: str):
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
    """
    return [
        "devcontainer",
        "exec",
        "--config",
        str(settings.config(repo)),
        "--workspace-folder",
        str(settings.workspace(repo)),
        "--remote-env",
        f"{STATE_DIR_VARIABLE}={ARTIFACTS_TARGET}/{job_id}",
        "--remote-env",
        f"{UNATTENDED_VARIABLE}=1",
        "--",
        "claude",
        "-p",
        f"{task_id}\n\n{brief}",
        "--agent",
        settings.agent,
        "--append-system-prompt-file",
        settings.invariants_file,
        "--output-format",
        "stream-json",
        "--verbose",
        "--json-schema",
        schema_argument(),
        "--permission-mode",
        "dontAsk",
        "--disallowedTools",
        DISALLOWED_TOOLS,
    ]


def kill_pattern(task_id: str) -> str:
    """Extended regex for ``pkill -f``: this task's ``claude -p`` command line.

    Anchored with ``^``: the command line must *start* with ``claude`` (or
    ``node <path>/claude``, how a script install runs), so a sibling's inlined
    brief containing ``claude -p <task> `` further along never matches. The task
    id is followed by whitespace, so task 12 never matches task 123. Task ids
    are plain names, so escaping ``.`` is the only metacharacter to handle.
    """
    task = task_id.replace(".", r"\.")
    return rf"^(([^ ]*/)?node )?([^ ]*/)?claude -p {task}[[:space:]]"


def kill_in_container(
    shell: Shell, container_id: str, task_id: str, env: dict[str, str]
) -> str:
    """Best effort: stop the job's ``claude`` inside the container. Returns a note.

    Killing the host ``devcontainer exec`` process does not reach the process it
    started in the container. ``pkill -f`` matches the ``claude -p <task>``
    command line, anchored at the start (see :func:`kill_pattern`) so a sibling
    job's argv that merely contains that text is never matched. Never verified,
    so the note says what is known.
    """
    if not container_id:
        return "no container id is known; the container-side process may still be alive"
    pattern = kill_pattern(task_id)
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
    The ``/artifacts`` bind's source must be the bundle root. Anything else, or
    no bind at all, is wrong. This checks paths only and cannot see whether the
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
    wanted_artifacts = os.path.realpath(artifacts_root())
    problems = []
    if binds.get(WORKSPACE_TARGET) != wanted_workspace:
        problems.append(
            f"{WORKSPACE_TARGET} is not a bind of {wanted_workspace} "
            f"(the --workspace-folder; found {binds.get(WORKSPACE_TARGET)!r})"
        )
    if binds.get(ARTIFACTS_TARGET) != wanted_artifacts:
        problems.append(
            f"{ARTIFACTS_TARGET} is not a bind of {wanted_artifacts}"
            f" (found {binds.get(ARTIFACTS_TARGET)!r})"
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
