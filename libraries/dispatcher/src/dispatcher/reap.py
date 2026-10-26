"""Reaping a job's worktree: only what is provably pushed.

The agent commits and pushes; the dispatcher then removes the worktree, because
a crashed agent cannot tidy itself. A reap that ran unconditionally would one
day remove hours of work from a run that failed just before its push, so the
new thing must exist before the old one goes: the branch must exist on the
remote and match the worktree's HEAD, and the worktree must have no
uncommitted changes. Otherwise the worktree is left alone and the result says
so.

Every git command runs inside the container, through ``devcontainer exec``
(design section 17, figure 10, steps 4 and 6). A worktree the agent made there
has a ``.git`` file holding in-container absolute paths (``/mnt/extra-addons/...``)
that do not resolve on the host, so ``git -C <worktree>`` on the host would
fail. The remote is also the one the container's git is configured to push to.
The reap runs before the engine's idle check, so the container is still up.

The host path (:func:`host_worktree`) is used for one thing only: to see,
through the bind mount, whether the worktree still exists. The orphan detail in
``queue.py`` uses it the same way.
"""

import os
import posixpath
from dataclasses import dataclass
from pathlib import Path

from .lifecycle import Shell, StageError, render_exec_prefix
from .settings import WORKSPACE_TARGET, Settings


@dataclass(frozen=True)
class ReapResult:
    reaped: bool
    note: str


def host_worktree(container_path: str, repo_dir: Path) -> Path | None:
    """Translate an in-container worktree path to its host path through the mount."""
    prefix = WORKSPACE_TARGET.rstrip("/") + "/"
    if not container_path.startswith(prefix):
        return None
    return repo_dir / container_path[len(prefix) :]


def _strictly_inside_workspace(container_path: str) -> bool:
    """True when the path is somewhere below the workspace mount, never the mount itself.

    A string check on the in-container path: ``..`` is rejected outright, so no
    path can lexically climb out of the mount.
    """
    root = WORKSPACE_TARGET.rstrip("/")
    if ".." in container_path.split("/"):
        return False
    path = posixpath.normpath(container_path)
    return path.startswith(root + "/") and path != root


def _git(shell: Shell, exec_prefix: list[str], args: list[str], env: dict[str, str]):
    """One git command inside the container."""
    return shell.run([*exec_prefix, "--", "git", *args], env=env, timeout=120)


def reap(
    shell: Shell,
    settings: Settings,
    repo: str,
    container_worktree: str,
    branch: str,
    env: dict[str, str] | None = None,
) -> ReapResult:
    """Remove the worktree if, and only if, its branch is pushed and identical."""
    env = dict(os.environ if env is None else env)
    if not container_worktree or not branch:
        return ReapResult(False, "no worktree or branch recorded; nothing reaped")
    if not _strictly_inside_workspace(container_worktree):
        return ReapResult(
            False, f"{container_worktree!r} is not a worktree under the mount"
        )
    host = host_worktree(container_worktree, settings.workspace(repo))
    if host is None or not host.is_dir():
        return ReapResult(
            False, f"worktree {container_worktree} does not exist; nothing to reap"
        )
    try:
        prefix = render_exec_prefix(settings, repo)
    except StageError as exc:
        return ReapResult(
            False, f"worktree {container_worktree} left in place: {exc.detail}"
        )
    problem = _unpushed_reason(shell, prefix, container_worktree, branch, env)
    if problem:
        return ReapResult(
            False, f"worktree {container_worktree} left in place: {problem}"
        )
    removed = _git(
        shell,
        prefix,
        ["-C", WORKSPACE_TARGET, "worktree", "remove", container_worktree],
        env,
    )
    if removed.returncode != 0:
        return ReapResult(
            False,
            f"worktree {container_worktree} left in place: "
            f"git worktree remove failed: {(removed.stderr or removed.stdout).strip()}",
        )
    return ReapResult(
        True, f"worktree {container_worktree} removed; {branch} is pushed"
    )


def _unpushed_reason(
    shell: Shell,
    exec_prefix: list[str],
    worktree: str,
    branch: str,
    env: dict[str, str],
) -> str:
    """Why the worktree is not provably pushed, or '' when it is."""
    status = _git(shell, exec_prefix, ["-C", worktree, "status", "--porcelain"], env)
    if status.returncode != 0:
        return f"git status failed: {_why(status)}"
    if status.stdout.strip():
        return "it has uncommitted changes"
    head = _git(shell, exec_prefix, ["-C", worktree, "rev-parse", "HEAD"], env)
    if head.returncode != 0:
        return f"git rev-parse failed: {_why(head)}"
    remote = _git(
        shell,
        exec_prefix,
        [
            "-C",
            WORKSPACE_TARGET,
            "ls-remote",
            "--heads",
            "origin",
            f"refs/heads/{branch}",
        ],
        env,
    )
    if remote.returncode != 0:
        return f"git ls-remote failed: {_why(remote)}"
    sha = remote.stdout.split()[0] if remote.stdout.strip() else ""
    if not sha:
        return f"branch {branch} does not exist on the remote"
    if sha != head.stdout.strip():
        return f"branch {branch} on the remote ({sha}) does not match the worktree HEAD"
    return ""


def _why(result) -> str:
    """The failure text of a command; ``devcontainer exec`` may write it to stdout."""
    return (result.stderr or result.stdout).strip()
