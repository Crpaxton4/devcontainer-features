"""Git puller: authored commits from every checkout under a root, plus repo labels.

``sync_git_log`` recursively discovers git checkouts, reads each one's
``git log --all`` over the resolved window and stores ``commit`` events keyed
``git:<sha>``. It is idempotent and follows the error-vs-skip contract of the
other pullers: an unusable tool returns ``{"error": reason}``, a single failing
repo only counts in ``failed_repos``.
"""

from __future__ import annotations

import os
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from odoo_dev_data.adapters._common import (
    _extract_task_ids,
    _parse_iso_utc,
    _run_capture,
    _store_pending,
    _window_bounds,
)
from odoo_dev_data.config import LocalConfig
from odoo_dev_data.events import EventRecord, EventStore

# ASCII unit separator used to delimit git-log fields (never appears in text).
_GIT_FIELD_SEP = "\x1f"


def derive_repo_label(remote_url: str) -> str:
    """Return the ``owner/repo`` label for a git remote URL.

    Strips a trailing ``.git`` and keeps the last two path segments so both ssh
    (``git@github.com:owner/repo.git``) and https
    (``https://github.com/owner/repo.git``) forms collapse to ``owner/repo``.
    Falls back to the cleaned URL when it has fewer than two segments.
    """
    cleaned = remote_url.strip()
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    # Treat ``:`` (scp-like ssh separator, scheme ``://``) as a path separator so
    # both URL shapes split into the same segment list.
    segments = [seg for seg in cleaned.replace(":", "/").split("/") if seg]
    if len(segments) >= 2:
        return "/".join(segments[-2:])
    return cleaned or "(unknown)"


def current_repo_label() -> str:
    """Return the normalized ``owner/repo`` label for the cwd's git remote, or ''.

    Best-effort display metadata for events written from a working tree: a
    non-git cwd or a missing remote is not an error. It yields ``""``, which
    sessionizes as a repo-less session. ssh and https clones of one repo
    converge because :func:`derive_repo_label` normalizes both URL shapes.
    """
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return ""
    label = derive_repo_label(result.stdout.strip())
    return "" if label == "(unknown)" else label


def _git_author_emails(config: Optional[LocalConfig]) -> list[str]:
    """Return the git author emails to filter commits by (issue #378 item 4).

    The configured identities that look like emails; when none are configured,
    falls back to the single ``git user.email``. Multiple emails are OR-ed by
    ``git log`` via repeated ``--author`` flags. Resolved ONCE per resync, not
    per discovered repo: global git config resolves outside any checkout, so a
    per-repo lookup would only repeat the same answer.
    """
    configured = [a for a in (config.resync_authors if config else []) if "@" in a]
    if configured:
        return configured
    email = _run_capture(["git", "config", "user.email"])
    return [email] if email else []


# Directories the repo discovery never descends into: the VCS dir itself and
# obviously-vendored/generated trees. Deliberately minimal (user decision on
# #652): a nested checkout anywhere ELSE — at any depth, including below
# another repo root — is a real repo and is discovered.
_PRUNE_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__"})


def _discover_git_repos(root: Path) -> list[Path]:
    """Return every git checkout at or under ``root`` (any depth), sorted (#652).

    A directory is a repo root when it contains a ``.git`` entry — a directory
    for a plain clone, or a *gitfile* for worktrees/submodules (``.exists()``
    covers both). Descent prunes only :data:`_PRUNE_DIRS`; discovery keeps
    walking below repo roots, so nested checkouts are found too (the branch
    overlap this can introduce dedupes on the ``git:<sha>`` unique index).
    ``followlinks=False`` guards against symlink loops. When ``root`` itself is
    a lone checkout the result is exactly ``[root]`` — the prior single-repo
    behavior.
    """
    repos: list[Path] = []
    for dirpath, dirnames, _filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE_DIRS]
        if (Path(dirpath) / ".git").exists():
            repos.append(Path(dirpath))
    return sorted(repos)


def _dedupe_shared_stores(repos: list[Path]) -> list[Path]:
    """Collapse checkouts sharing one object store onto a single root (#652).

    Linked worktrees (gitfile ``.git``) share the main clone's store, so reading
    each would return the same commits once per worktree — inflating ``found``
    and wasting subprocesses (``git:<sha>`` dedup keeps the DB correct either
    way). Keyed on ``git rev-parse --git-common-dir``: linked worktrees resolve
    to the main clone's ``.git`` and collapse onto the first (sorted) root,
    while submodules keep their own ``modules/<name>`` dir and survive. A repo
    whose rev-parse fails falls back to its own path (kept).
    """
    seen: set[str] = set()
    kept: list[Path] = []
    for root in repos:
        common = _run_capture(
            [
                "git",
                "-C",
                str(root),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ]
        )
        key = common or str(root)
        if key not in seen:
            seen.add(key)
            kept.append(root)
    return kept


def _git_log(
    root: Path, emails: list[str], since: datetime, until: datetime
) -> Optional[str]:
    """Return commits by ``emails`` across ALL branches of the repo at ``root``.

    ``-C root`` scopes the read to one discovered checkout regardless of the
    process cwd (#652). ``--all`` (issue #378 item 2) makes unmerged branch work
    visible — the work most likely to be unlogged — while ``git:<sha>`` external
    ids dedupe the branch overlap ``--all`` introduces. The full-ISO
    ``--since``/``--until`` pair applies the resolved window exactly.
    """
    pretty = _GIT_FIELD_SEP.join(("%H", "%aI", "%s"))
    cmd = [
        "git",
        "-C",
        str(root),
        "log",
        "--all",
        f"--pretty={pretty}",
        f"--since={since.isoformat()}",
        f"--until={until.isoformat()}",
    ]
    cmd.extend(f"--author={email}" for email in emails)
    return _run_capture(cmd)


def _repo_label_for(root: Path) -> str:
    """Derive one discovered repo's ``owner/repo`` label from its own origin.

    Deliberately does NOT consult the DB ``repo_label`` setting: that is a
    single global value, and stamping it onto every discovered repo would label
    them all identically. Each repo's label comes from its OWN ``origin`` remote
    (empty when it has none).
    """
    remote = _run_capture(["git", "-C", str(root), "remote", "get-url", "origin"])
    return derive_repo_label(remote) if remote else ""


def _build_commit_event(line: str, label: str) -> Optional[EventRecord]:
    """Build one ``commit`` event from a git-log line, or None when malformed.

    A valid line has three fields: sha, authored date, subject. The commit's
    SUBJECT is the only text task ids are read from (#1040). ``%D`` ref
    decorations used to be read alongside it and recorded as ``branch``, but a
    decoration is whatever refs happen to point at that commit *now* — so a
    ``Merge pull request #NNN from <org>/UAT`` commit on ``main`` was attributed
    to a task purely because its decoration still read
    ``HEAD -> <task-id>-branch, origin/main``, and every repo-wide merge
    inherited whatever task branch was checked out. Extra fields on a line are
    ignored rather than rejected, so a log captured with the old four-field
    pretty format still parses. Task ids are validated later by
    :func:`_store_pending`.
    """
    parts = line.split(_GIT_FIELD_SEP)
    if len(parts) < 3:
        return None
    sha, authored, subject = parts[0], parts[1], parts[2]
    return EventRecord(
        id=None,
        source="commit",
        timestamp=_parse_iso_utc(authored),
        task_ids=_extract_task_ids(subject, "", allow_leading_id=True),
        repo=label,
        subject=subject,
        external_id=f"git:{sha}",
    )


def _repo_commit_events(
    root: Path, emails: list[str], since: datetime, until: datetime
) -> Optional[list[EventRecord]]:
    """Read one discovered repo's commits as events, or None when its log fails."""
    log = _git_log(root, emails, since, until)
    if log is None:
        return None
    label = _repo_label_for(root)
    return [
        event
        for line in log.splitlines()
        if line
        if (event := _build_commit_event(line, label)) is not None
    ]


def sync_git_log(
    state: EventStore,
    config: Optional[LocalConfig] = None,
    client: Any = None,
    *,
    root: Optional[Path] = None,
    now: Optional[datetime] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> dict[str, Any]:
    """Reconcile authored commits from every repo under the cwd (#378, #652).

    Recursively discovers git checkouts at/below the current directory and reads
    each one's ``git log --all`` over the resolved window, filtered to the
    configured author emails and labeled from each repo's OWN origin remote.
    Commits store as ``commit`` events keyed ``git:<sha>`` (task ids validated
    in one batch when ``client`` is supplied). Idempotent. Entirely-unusable git
    — no author identity, zero repos, or every repo's log failing — is a hard
    ``{"error": reason}``; a single failing repo only counts in
    ``failed_repos``. ``found`` vs ``inserted`` distinguishes "nothing to
    capture" from "captured nothing new" (#652 item 4).
    """
    try:
        since, until = _window_bounds(config, now, start, end)
    except ValueError as exc:
        return {"error": str(exc)}
    emails = _git_author_emails(config)
    if not emails:
        return {"error": "git unavailable or user.email unset"}
    top = root or Path.cwd()
    repos = _dedupe_shared_stores(_discover_git_repos(top))
    if not repos:
        return {"error": f"no git repositories under {top}"}
    pending: list[EventRecord] = []
    failed = 0
    for root in repos:
        events = _repo_commit_events(root, emails, since, until)
        if events is None:
            failed += 1
        else:
            pending.extend(events)
    if failed == len(repos):
        return {"error": f"git log failed in all {len(repos)} repositories"}
    result: dict[str, Any] = {
        "inserted": len(_store_pending(state, pending, client)),
        "found": len(pending),
        "repos": len(repos),
    }
    if failed:
        result["failed_repos"] = failed
    return result
