"""Pure attribution helpers: branch, repo and task-id labels for an event.

The helpers read the working tree through ``git`` only. They never touch the
event store.
"""

import re
import subprocess
from typing import Any, Iterable, Optional

from .ports import GitGateway


def _git_text(*args: str) -> str:
    """Run a read-only ``git`` command, returning stripped stdout or ``""``.

    Every failure mode a non-repo working directory can produce (non-zero exit,
    no ``git`` on ``PATH``, an OS-level spawn error) collapses to ``""``: the
    provenance fields are best-effort display metadata on the event-write path
    and must never turn a successful tool call into an error (#509).
    """

    try:
        result = subprocess.run(
            ["git", *args], capture_output=True, text=True, check=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return ""
    return result.stdout.strip()


def current_branch_label() -> str:
    """Return the branch checked out in the cwd, or ``""`` when there is none.

    The ``branch`` counterpart to
    :meth:`~odoo_dev_core.ports.GitGateway.current_repo_label`, and best-effort in the same
    way: a detached HEAD or a non-repo cwd yields ``""`` rather than a bogus
    branch name.

    ``symbolic-ref`` is asked rather than ``rev-parse --abbrev-ref`` because it
    answers the question this path actually asks. It fails outright on a
    detached HEAD instead of printing the literal ``HEAD`` as if that were a
    branch, and it still names the branch on a repo with no commits yet, where
    ``rev-parse`` cannot resolve the revision at all.
    """

    return _git_text("symbolic-ref", "--short", "HEAD")


def normalize_repo_label(value: Optional[str], git: GitGateway) -> Optional[str]:
    """Return the canonical ``owner/repo`` label for a caller-stated repo (#742).

    The core-layer door the surfaces normalize an incoming repo through, so the
    CLI never has to reach past core into the git adapter (ADR-005 rule
    4). The ``git`` gateway derives the label. The caller may hand over whatever it happens to have: the hook shim
    states the raw ``git remote get-url origin`` URL of the *session's* cwd
    (which is not the shim's own cwd, so the SDK cannot re-derive it), and a
    remote-less checkout falls back to the bare directory name.

    * A git URL — ssh (``git@github.com:owner/repo.git``) or https
      (``https://github.com/owner/repo.git``) — collapses to ``owner/repo``.
    * An already-normalized ``owner/repo`` label passes through unchanged.
    * A single-segment label (the shim's no-remote basename fallback) is kept
      verbatim.
    * ``None`` or blank yields ``None``, which is
      the ``log_event`` handler's "resolve it from the working tree"
      sentinel — an absent ``--repo`` must not record the literal
      ``"(unknown)"`` that a blank URL would otherwise derive to.

    :param value: Raw git remote URL, plain repo label, blank, or ``None``.
    :type value: Optional[str]
    :param git: The git gateway whose ``derive_repo_label`` applies the rule.
    :return: The ``owner/repo`` label, or ``None`` to defer to cwd derivation.
    :rtype: Optional[str]
    """

    if value is None or not value.strip():
        return None
    return git.derive_repo_label(value)


def normalize_task_ids(values: Optional[Iterable[Any]]) -> list[str]:
    """Return the int-coercible members of ``values`` as canonical id strings.

    The one coercion rule for explicit attribution hints, shared by every
    frontend (#507). A value that does not name a task — ``None``, a free-text
    string, a stray object — is dropped rather than persisted as a task id that
    can never join a real task, and an id that *is* coercible is normalized
    through ``int`` so ``5``, ``"5"``, and ``" 5 "`` all record as ``"5"``.
    """

    normalized = []
    for value in values or []:
        try:
            normalized.append(str(int(value)))
        except (TypeError, ValueError):
            continue
    return normalized


# Minimum digit count for a task id recovered from a branch, mirroring the resync
# puller (``odoo_dev_data.adapters._common._MIN_TASK_ID_DIGITS``): a shorter run is almost
# always a stray number, and using the same floor keeps a hook event and the
# commit it accompanies on the SAME task lane rather than splitting them (#378).
_MIN_TASK_ID_DIGITS = 4

# The ``<task-id>-<slug>`` branch convention task branches follow (#622: id
# BEFORE the ``-``), anchored to a token start (``^`` or a ``/`` path separator)
# so a digit run buried mid-token (e.g. ``fixes-1234-x``) is never read as a
# task id. Kept in lockstep with the same ``(\d+)-`` form in
# ``odoo_dev_data.adapters._common._TASK_ID_PATTERNS`` so hook and resync attribution
# agree on a session branch.
_BRANCH_TASK_ID_PATTERN = re.compile(rf"(?:^|[\s,/])(\d{{{_MIN_TASK_ID_DIGITS},}})-")

# Conventional-commit types. A branch whose FIRST path segment is one of these
# follows the GitHub-issue convention ``<type>/<issue-number>-<slug>``, so its
# leading digit run is an issue number in the hosting forge, not an Odoo task id
# (#1040: ``docs/1019-shellcheck-form-per-file-set`` minted phantom task lanes
# 1016/1019/1020/1029 in a repo with no Odoo project at all). A task branch is
# ``<task-id>-<slug>`` at the root (#622), never such a branch, so excluding the
# type prefixes costs no real attribution. Note ``feat`` is listed but ``feature`` is not: only the
# conventional-commit spelling carries the convention.
_CONVENTIONAL_BRANCH_TYPES = frozenset(
    {
        "build",
        "chore",
        "ci",
        "docs",
        "feat",
        "fix",
        "perf",
        "refactor",
        "style",
        "test",
    }
)


def task_ids_from_branch(branch: Optional[str]) -> list[str]:
    """Recover the task ids named by a ``<task-id>-<slug>`` session branch.

    A task branch is named ``f"{task_id}-{slug}"`` (#622), so the task identity
    is derivable from the branch (#574). This is the last attribution signal
    consulted before an unhinted event falls through to untargeted triage.

    Returns the distinct ids in first-seen order, canonicalized through ``int`` so
    a zero-padded ``0028788-…`` records as ``"28788"``. A branch that does not
    match the convention — a ``main``/``feat/x`` branch, a detached HEAD's ``""``,
    ``None`` — yields ``[]``. So does a branch whose first path segment is a
    conventional-commit type (:data:`_CONVENTIONAL_BRANCH_TYPES`): that names a
    GitHub issue, not an Odoo task (#1040).

    :param branch: The session branch, or ``None``/`""` when there is none.
    :type branch: Optional[str]
    :return: The recovered task ids as canonical id strings, first-seen order.
    :rtype: list[str]
    """

    if not branch:
        return []
    if branch.split("/", 1)[0].strip().lower() in _CONVENTIONAL_BRANCH_TYPES:
        return []
    ids: list[str] = []
    for raw in _BRANCH_TASK_ID_PATTERN.findall(branch):
        canonical = str(int(raw))
        if canonical not in ids:
            ids.append(canonical)
    return ids


def resolve_task_ids(
    task_ids: Optional[Iterable[Any]] = None,
    attach_active_run: bool = True,
    branch: Optional[str] = None,
) -> list[str]:
    """Resolve which tasks an event attributes to - the one attribution rule.

    1. An explicit hint wins. ``task_ids`` is normalized by
       :func:`normalize_task_ids`, so a caller may hand over whatever its
       interface produced without pre-validating it.
    2. Otherwise the ``<task-id>-<slug>`` convention of the caller-stated
       ``branch`` recovers the task id (#574).
    3. With none of the above (or with ``attach_active_run`` disabled) the
       scope is empty: untargeted session-level activity.

    Only the branch the caller *states* is consulted. The working-tree branch
    that ``log_event`` resolves for the provenance column is deliberately not,
    so an incidental checkout never re-attributes an event that was never
    bound to that task.

    :param task_ids: Explicit attribution hint; non-task values are dropped.
    :param attach_active_run: Whether an unhinted event falls back to ``branch``.
    :param branch: The caller-stated ``<task-id>-<slug>`` session branch.
    :return: The task ids the event attributes to, as id strings.
    """
    explicit = normalize_task_ids(task_ids)
    if explicit:
        return explicit
    if not attach_active_run:
        return []
    return task_ids_from_branch(branch)
