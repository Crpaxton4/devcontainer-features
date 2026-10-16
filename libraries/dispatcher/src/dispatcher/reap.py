"""Reaping a job's worktree: only what is provably pushed.

The agent commits and pushes; the dispatcher then removes the worktree, because
a crashed agent cannot tidy itself. A reap that ran unconditionally would one
day remove hours of work from a run that failed just before its push, so the
new thing must exist before the old one goes: the branch must exist on the
remote and match the worktree's HEAD. Otherwise the worktree is left alone and
the result says so.

Known limitation, and a safe one: a worktree the agent made inside the container
has a ``.git`` file holding in-container absolute paths that do not resolve on
the host, so ``git -C <worktree>`` fails here and the worktree is left alone.
In practice this module will rarely reap. No path in it reaps unpushed work,
which is the half that matters; the gap is only that cleanup does not happen.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from .lifecycle import Shell
from .settings import WORKSPACE_TARGET


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


def _strictly_inside(path: Path, root: Path) -> bool:
    """True when ``path`` resolves to somewhere below ``root`` (never ``root`` itself)."""
    return path.resolve().is_relative_to(root.resolve()) and (
        path.resolve() != root.resolve()
    )


def _git(shell: Shell, args: list[str], env: dict[str, str]):
    return shell.run(["git", *args], env=env, timeout=120)


def reap(
    shell: Shell,
    repo_dir: Path,
    container_worktree: str,
    branch: str,
    env: dict[str, str] | None = None,
) -> ReapResult:
    """Remove the worktree if, and only if, its branch is pushed and identical."""
    env = dict(os.environ if env is None else env)
    if not container_worktree or not branch:
        return ReapResult(False, "no worktree or branch recorded; nothing reaped")
    worktree = host_worktree(container_worktree, repo_dir)
    if worktree is None or not _strictly_inside(worktree, repo_dir):
        return ReapResult(
            False, f"{container_worktree!r} is not a worktree under the mount"
        )
    if not worktree.is_dir():
        return ReapResult(False, f"worktree {worktree} does not exist; nothing to reap")
    problem = _unpushed_reason(shell, repo_dir, worktree, branch, env)
    if problem:
        return ReapResult(False, f"worktree {worktree} left in place: {problem}")
    removed = _git(
        shell, ["-C", str(repo_dir), "worktree", "remove", str(worktree)], env
    )
    if removed.returncode != 0:
        return ReapResult(
            False, f"worktree {worktree} left in place: {removed.stderr.strip()}"
        )
    return ReapResult(True, f"worktree {worktree} removed; {branch} is pushed")


def _unpushed_reason(
    shell: Shell, repo_dir: Path, worktree: Path, branch: str, env: dict[str, str]
) -> str:
    """Why the worktree is not provably pushed, or '' when it is."""
    status = _git(shell, ["-C", str(worktree), "status", "--porcelain"], env)
    if status.returncode != 0:
        return f"git status failed: {status.stderr.strip()}"
    if status.stdout.strip():
        return "it has uncommitted changes"
    head = _git(shell, ["-C", str(worktree), "rev-parse", "HEAD"], env)
    if head.returncode != 0:
        return f"git rev-parse failed: {head.stderr.strip()}"
    remote = _git(
        shell,
        ["-C", str(repo_dir), "ls-remote", "--heads", "origin", f"refs/heads/{branch}"],
        env,
    )
    if remote.returncode != 0:
        return f"git ls-remote failed: {remote.stderr.strip()}"
    sha = remote.stdout.split()[0] if remote.stdout.strip() else ""
    if not sha:
        return f"branch {branch} does not exist on the remote"
    if sha != head.stdout.strip():
        return f"branch {branch} on the remote ({sha}) does not match the worktree HEAD"
    return ""
