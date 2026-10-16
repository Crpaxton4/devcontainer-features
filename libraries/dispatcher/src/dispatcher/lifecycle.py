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
  root-owned directory, so the mounts are asserted after the container is up.
  The dispatcher asserts the mount; it never creates it.
* ``--append-system-prompt-file`` is required here. It was retired for the global
  ``claude`` wrapper (#740) because a flag reaches only the session the wrapper
  starts, never a forked subagent. The dispatcher is neither wrapper.
* Two independent idempotent lifecycle checks, not a state machine: ``up`` on
  dispatch, and stop when the project has nothing running and nothing pending.
  Stop, never remove, and never touch the shared sidecars.
"""

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from odoo_sdk.state.dispatch_state import artifacts_root

from .schema import schema_argument
from .settings import (
    ARTIFACTS_TARGET,
    Settings,
    normalize_project,
)

DISALLOWED_TOOLS = "Bash(docker:*)"
STATE_DIR_VARIABLE = "ODOO_DEV_STATE_DIR"
ACTIVE_REPOS_VARIABLE = "ODOO_ACTIVE_REPOS"


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
        return subprocess.Popen(
            argv,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )


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
    """The dispatch command line of design section 8, exactly."""
    return [
        "devcontainer",
        "exec",
        "--config",
        str(settings.config(repo)),
        "--workspace-folder",
        str(settings.workspace(repo)),
        "--remote-env",
        f"{STATE_DIR_VARIABLE}={ARTIFACTS_TARGET}/{job_id}",
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
    result = shell.run(render_up(settings, repo), env=env)
    if result.returncode != 0:
        raise ContainerStartError(_failure_text("devcontainer up", result))
    return _container_id(result)


def _bind_sources(mounts: list[dict]) -> dict[str, str]:
    return {
        str(mount.get("Destination", "")): os.path.realpath(mount.get("Source", ""))
        for mount in mounts
        if mount.get("Type") == "bind"
    }


def assert_mounts(
    shell: Shell, settings: Settings, repo: str, container_id: str, env: dict[str, str]
) -> None:
    """Assert, after the container is up, that the binds are the ones we asked for.

    The workspace mount's source must be the real ``--workspace-folder`` and the
    ``/artifacts`` mount's source must be the bundle root. Anything else, or no
    bind at all, is the silent empty-directory case. Asserted, never created.
    """
    result = shell.run(
        ["docker", "inspect", "--format", "{{json .Mounts}}", container_id], env=env
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
    if wanted_workspace not in binds.values():
        problems.append(
            f"no bind mount has source {wanted_workspace} (the --workspace-folder)"
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
    done = shell.run(["docker", "compose", "-p", project, "stop"], env=env)
    if done.returncode == 0:
        return True
    return shell.run(["docker", "stop", container_name(repo)], env=env).returncode == 0


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
