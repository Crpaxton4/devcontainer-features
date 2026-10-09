"""MCP ``start_task`` tool: interaction surface composing search + start commands.

This module owns all MCP-specific concerns for starting a task — argument
elicitation (project/task disambiguation on the name-search path only), git
branch setup, and optional AI branch-name generation via fastmcp 4's two-phase
sampling flow (SEP-2322) — and then delegates the actual Odoo/state mutation to
the atomic :class:`StartTaskCommand`. Commands never see the FastMCP ``ctx``;
primitives resolved here are passed to the command.

Sampling flow (#664): fastmcp 4 removed the imperative ``ctx.sample`` back-
channel. When the client advertises the ``sampling`` capability *and* the
connection negotiated a protocol era that carries multi-round-trip results
(2026-07-28+), the first invocation returns an
:class:`~mcp.types.InputRequiredResult` holding one ``CreateMessageRequest``
keyed ``branch_description`` — side-effect free: no run row, no branch
mutation. The client fulfills it and re-invokes the tool with
``ctx.input_responses`` populated; the continuation slugifies the sampled text
and executes the full start flow exactly once. Whenever sampling is
unavailable (no capability, handshake-era connection, empty/garbage response)
the deterministic ``_slugify(task_name)`` fallback is used instead, so the
flow never hard-fails on this optional capability.

Idempotent lifecycle entry point (#621): the current session state is checked
BEFORE any side effect, and an already-RUNNING session short-circuits to a
no-op result (``already_running: true``) with no branch mutation and no
prompts. There is no confirmation gate, and the ``task_id``-only path performs
zero name searches and zero elicitations (#614), so automation can call this
headless in any state.

Base-branch precedence (#903/#979): the explicit ``base_branch`` argument, then
``ODOO_SDK_BASE_BRANCH``, then the odoo-dev repo map's ``default_branch`` for
this checkout (``$ODOO_DEV_STATE_DIR/repo-map.json``), then ``origin/HEAD``,
then the current branch. The map ranks above ``origin/HEAD`` because the repos
this tool serves routinely keep a GitHub default (``master``) that task work is
never based on, so the remote default is a confidently wrong answer; it ranks
below the two explicit knobs because those are a human overriding the record.
See :func:`_resolve_base_branch`.
"""

import json
import os
import re
import subprocess
from typing import Any, Callable, NamedTuple, Optional, Union

from fastmcp import Context
from mcp.types import (
    ClientCapabilities,
    CreateMessageRequest,
    CreateMessageRequestParams,
    InputRequiredResult,
    SamplingCapability,
    SamplingMessage,
    TextContent,
)
from mcp.types.version import MODERN_PROTOCOL_VERSIONS
from pydantic import BaseModel

from odoo_sdk.commands import Registry
from odoo_sdk.tracking.models import TaskState

from .composition import composition_tool


class _BranchSetupError(RuntimeError):
    """Raised when task-branch setup failed *and* the repo was restored (#542).

    Carries a caller-facing message describing where the user's work ended up.
    :func:`_setup_task_branch` converts it into the flow's ordinary error string
    rather than letting it escape: by the time it is raised the working tree is
    back on the original branch, so there is nothing left for the caller's
    rollback path to undo.
    """


class _SelectIndex(BaseModel):
    """One-field schema for picking a numbered item (project, task, or branch)."""

    selection: int


#: Task-branch naming convention (#622): ``<task-id>-<slug>``. Kept in lockstep
#: with the branch parsers in ``commands.log_event`` and
#: ``adapters.external_sync``.
_TASK_BRANCH_RE = re.compile(r"\d+-")

#: Environment variable naming the branch task branches must fork from (#903).
#: Set it once per project (the pre-production branch on repos whose base is not
#: the GitHub default) and every headless ``start_task`` forks from it.
_BASE_BRANCH_ENV = "ODOO_SDK_BASE_BRANCH"

#: Directory holding the odoo-dev plugin's state, ``repo-map.json`` among it
#: (#979). Read with NO default on purpose: ``plugins/odoo-dev/scripts/
#: state-dir.sh`` is the single definition of the ``$HOME/.local/share/odoo-dev``
#: fallback and forbids every other caller from spelling it a second time — in a
#: devcontainer the variable is exported for you, and where it is unset there is
#: no map this process may assume exists.
_STATE_DIR_ENV = "ODOO_DEV_STATE_DIR"

#: The odoo-dev repo map, relative to :data:`_STATE_DIR_ENV`.
_REPO_MAP_FILE = "repo-map.json"

#: The vocabulary :class:`_ResolvedBase` reports a base's provenance in, in
#: precedence order. These strings are caller-facing: they are quoted verbatim
#: in the branch-setup failure message so an unexpected base (the #979 symptom)
#: names the knob that chose it.
_SOURCE_ARGUMENT = "argument"
_SOURCE_ENV = _BASE_BRANCH_ENV
_SOURCE_REPO_MAP = "repo map"
_SOURCE_SELECTION = "branch selection"
_SOURCE_ORIGIN_HEAD = "origin/HEAD"
_SOURCE_CURRENT_BRANCH = "current branch"


class _ResolvedBase(NamedTuple):
    """A base branch together with the knob that chose it (#903/#979).

    Threaded from :func:`_resolve_base_branch` down to the failure message so a
    collision can say *which* base it forked from and why that base was picked;
    the success path still surfaces the bare ``branch`` in the result payload.
    """

    branch: str
    source: str


def _git(*args: str) -> subprocess.CompletedProcess:
    """Run a read-only ``git`` command, capturing its text output."""
    return subprocess.run(["git", *args], capture_output=True, text=True)


def _current_branch() -> Optional[str]:
    result = _git("rev-parse", "--abbrev-ref", "HEAD")
    name = result.stdout.strip()
    return name if result.returncode == 0 and name != "HEAD" else None


def _repo_root() -> Optional[str]:
    """Real path of the working tree's git root, or ``None`` outside a repo.

    Resolved through ``realpath`` so it compares equal to a repo-map
    ``repo_path`` that reaches the same checkout through a symlink or a bind
    mount (``/mnt/extra-addons`` in practice).
    """
    result = _git("rev-parse", "--show-toplevel")
    path = result.stdout.strip()
    if result.returncode != 0 or not path:
        return None
    return os.path.realpath(path)


def _list_local_branches() -> list[str]:
    """List local branches eligible as a fork base (task branches excluded)."""
    result = _git("branch", "--format=%(refname:short)")
    if result.returncode != 0:
        return []
    branches = [
        b.strip()
        for b in result.stdout.splitlines()
        if b.strip() and not _TASK_BRANCH_RE.match(b.strip())
    ]
    return sorted(branches, key=lambda b: (len(b), b))


def _is_dirty() -> bool:
    result = _git("status", "--porcelain")
    return result.returncode == 0 and bool(result.stdout.strip())


def _branch_exists(branch_name: str) -> bool:
    """True when ``branch_name`` already exists locally."""
    result = _git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch_name}")
    return result.returncode == 0


def _stash_count() -> int:
    """Return the number of entries currently on the git stash (``0`` on error)."""
    result = _git("stash", "list")
    if result.returncode != 0:
        return 0
    return sum(1 for line in result.stdout.splitlines() if line.strip())


def _rollback_task_branch(branch_name: str, original_branch: Optional[str]) -> None:
    """Undo a branch created this run: switch back, then delete ``branch_name``.

    Best-effort cleanup when ``start_task`` fails downstream of branch setup
    (#164), so a raised command does not leave a dangling task branch checked
    out. Switching off ``branch_name`` first lets ``git branch -D`` succeed
    (git refuses to delete the current branch).

    :param branch_name: The task branch created this run, to be deleted.
    :type branch_name: str
    :param original_branch: Branch to return to; skipped when ``None`` (e.g. a
        prior detached HEAD).
    :type original_branch: Optional[str]
    """
    if original_branch is not None:
        _git("checkout", original_branch)
    _git("branch", "-D", branch_name)


def _rollback_task_branch_if_created(
    branch_name: Optional[str], created: bool, original_branch: Optional[str]
) -> None:
    """Roll back the task branch only when this run created it (#164).

    The guard lives here rather than inline in the tool's ``except`` so the
    cleanup path reads as a single call: a branch that already existed — or
    was never created because the working tree was already on it
    (``branch_name is None``) — must survive the failure untouched.

    :param branch_name: Task branch in play, or ``None`` when none was set up.
    :type branch_name: Optional[str]
    :param created: Whether this run created ``branch_name``.
    :type created: bool
    :param original_branch: Branch to return to; skipped when ``None``.
    :type original_branch: Optional[str]
    """
    if created and branch_name is not None:
        _rollback_task_branch(branch_name, original_branch)


def _resolve_base_ref(base_branch: str) -> str:
    """Fetch ``base_branch`` from ``origin`` and return the ref to fork from.

    Forks must start from the *remote* tip (#454): a local base-branch pointer
    in a long-lived devcontainer commonly drifts behind ``origin`` (observed
    −60 commits), so forking from the local ref silently omits merged work and
    the new branch appears to have "lost" it. We ``git fetch origin <base>``
    (a read-only network op that leaves the working tree and untracked files
    untouched) and fork from ``origin/<base>`` when that remote-tracking ref
    then exists. When there is no ``origin`` remote — or the fetch/ref lookup
    fails, e.g. offline — we fall back to the local ``base_branch`` rather than
    hard-failing, so single-repo and disconnected setups still work.

    :param base_branch: Local base branch the user chose to fork from.
    :type base_branch: str
    :return: ``origin/<base_branch>`` when reachable, else ``base_branch``.
    :rtype: str
    """
    _git("fetch", "origin", base_branch)
    remote_ref = f"origin/{base_branch}"
    probe = _git("rev-parse", "--verify", "--quiet", f"refs/remotes/{remote_ref}")
    return remote_ref if probe.returncode == 0 else base_branch


def _unwind_failed_pop(
    branch_name: str,
    created: bool,
    original_branch: Optional[str],
    base_branch: str,
    base_source: str,
) -> str:
    """Put the repo back where it started after ``git stash pop`` failed (#542).

    Forking from ``origin/<base>`` (#454) can bring in a *tracked* file at the
    same path as a local *untracked* one; the auto-stash pop then aborts with a
    collision, leaving the user on the task branch looking at origin's version
    of their file while their own copy survives only in ``stash@{0}`` — the
    "my work disappeared" symptom of #454.

    A failed pop never drops its stash entry, so every local change is still
    recorded there and ``git checkout --force`` can safely discard whatever the
    partial pop left behind. We switch back, delete the branch when this run
    created it, and re-pop on the original branch, where the colliding path is
    untracked again and the pop applies cleanly.

    :param branch_name: Task branch that was checked out when the pop failed.
    :type branch_name: str
    :param created: Whether this run created ``branch_name`` (only then is it
        deleted — a pre-existing branch must survive).
    :type created: bool
    :param original_branch: Branch to return to; skipped when ``None``.
    :type original_branch: Optional[str]
    :param base_branch: Base that was forked from — named in the message because
        an unexpected base is the usual cause of the collision (#979): a repo
        whose GitHub default is ``master`` while task work flows into ``E2E``
        collides on every file the two branches disagree about.
    :type base_branch: str
    :param base_source: Where that base came from, e.g. ``origin/HEAD`` — see
        :func:`_resolve_base_branch` for the vocabulary.
    :type base_source: str
    :return: Caller-facing message naming where the user's work now lives.
    :rtype: str
    """
    undone = False
    if original_branch is not None:
        _git("checkout", "--force", original_branch)
        # ``git branch -D`` refuses to delete the branch you are standing on, so
        # this only runs once the checkout above has moved us off it.
        undone = not created or _git("branch", "-D", branch_name).returncode == 0
    if _git("stash", "pop").returncode == 0:
        where = "your work is back in the working tree"
    else:
        where = "your work is kept in 'stash@{0}' — recover it with `git stash pop`"
    left = (
        f"the repository is back on {original_branch!r}"
        if undone
        else f"branch {branch_name!r} could not be cleaned up"
    )
    return (
        f"Could not set up branch {branch_name!r}: local changes collide with "
        f"files already tracked on base {base_branch!r} chosen from "
        f"{base_source}. Now {left} and {where}. "
        f"Commit or move the conflicting files, then retry."
    )


def _set_upstream(branch_name: str, base_ref: str) -> None:
    """Point ``branch_name``'s upstream at the base's remote ref (#903).

    Without this the freshly created branch inherits whatever upstream git
    infers — in practice the remote default — so ``git pull --rebase`` and PR
    tooling that reads ``branch.<name>.merge`` silently target the wrong base
    (the stray-PR condition #903 reports). Only a remote ref is meaningful as
    an upstream, so a local-only fallback base (offline / no ``origin``, see
    :func:`_resolve_base_ref`) is skipped rather than recorded.

    Best-effort by design: the branch is already created and checked out at
    this point, and a repo that cannot record an upstream (no remote-tracking
    ref yet, ancient git) must not fail the whole start flow over it.

    :param branch_name: The freshly created task branch.
    :type branch_name: str
    :param base_ref: Ref the branch was forked from, e.g. ``origin/uat``.
    :type base_ref: str
    """
    if not base_ref.startswith("origin/"):
        return
    _git("branch", f"--set-upstream-to={base_ref}", branch_name)


def _create_task_branch(
    branch_name: str, base_branch: str, base_source: str = _SOURCE_ARGUMENT
) -> bool:
    """Create or switch to ``branch_name``, preserving any local changes.

    Idempotent (#149): when ``branch_name`` already exists it is checked out
    instead of re-created (``git checkout -b`` aborts with exit 128 otherwise).
    Stash-safe (#150): ``git stash push -u`` carries untracked files, and the
    matching ``pop`` runs only when an entry was actually pushed — a plain
    stash on an untracked-only tree saves nothing, so an unconditional ``pop``
    would fail with "No stash entries found".
    Remote-based (#454): a freshly created branch forks from the fetched
    ``origin/<base>`` tip (see :func:`_resolve_base_ref`), never the possibly
    stale local base ref.
    Base-tracking (#903): the new branch's upstream is set to that same
    ``origin/<base>`` (see :func:`_set_upstream`), so a later ``git pull
    --rebase`` and any PR tooling reading the upstream target the chosen base
    rather than the remote default.
    Atomic (#542): a pop that fails on the new branch is unwound by
    :func:`_unwind_failed_pop` rather than stranding the user mid-switch.

    :param branch_name: Target branch to end up on.
    :type branch_name: str
    :param base_branch: Base branch to fork from when creating a new branch.
    :type base_branch: str
    :param base_source: Where ``base_branch`` came from, for the failure message
        (#979). Defaults to :data:`_SOURCE_ARGUMENT`, which is what a direct
        call naming the base literally is.
    :type base_source: str
    :return: ``True`` when a new branch was created, ``False`` when an existing
        one was merely checked out — lets callers roll back only fresh branches.
    :rtype: bool
    :raises _BranchSetupError: When the auto-stash could not be re-applied; the
        repo has been returned to ``original_branch`` before it is raised.
    """
    original_branch = _current_branch()
    stashed = False
    if _is_dirty():
        before = _stash_count()
        subprocess.run(
            ["git", "stash", "push", "-u", "-m", f"auto-stash: {branch_name}"],
            check=True,
        )
        stashed = _stash_count() > before
    created = not _branch_exists(branch_name)
    try:
        if created:
            base_ref = _resolve_base_ref(base_branch)
            subprocess.run(["git", "checkout", "-b", branch_name, base_ref], check=True)
            _set_upstream(branch_name, base_ref)
        else:
            subprocess.run(["git", "checkout", branch_name], check=True)
    except BaseException:
        # The checkout itself failed, so we are still on the original branch:
        # a best-effort pop (never ``check=True``, which would mask the real
        # git error behind a second one) puts the stashed work straight back
        # where it came from. Should even that fail, the entry stays on the
        # stash and the original error — now a boundary error (#541) — is what
        # the caller sees.
        if stashed:
            _git("stash", "pop")
        raise
    if stashed and _git("stash", "pop").returncode != 0:
        raise _BranchSetupError(
            _unwind_failed_pop(
                branch_name, created, original_branch, base_branch, base_source
            )
        )
    return created


def _slugify(text: str) -> str:
    """Lowercase, hyphenate, and trim ``text`` into a git-safe branch suffix."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:45]


#: Key under which the branch-description sampling request travels through the
#: multi-round-trip guard channel (SEP-2322): it names the entry in the
#: ``input_requests`` map the first invocation returns and the matching entry in
#: ``ctx.input_responses`` the continuation reads back.
_BRANCH_DESCRIPTION_KEY = "branch_description"


def _client_supports_sampling(ctx: Any) -> bool:
    """True only when the MCP client advertised the ``sampling`` capability.

    Sampling (a client-fulfilled LLM completion) is optional; clients like
    Claude Code do not advertise it. Probing here lets us degrade gracefully
    to the deterministic slug instead of minting a ``CreateMessageRequest``
    the client would never fulfill.
    """
    try:
        return bool(
            ctx.session.check_client_capability(
                ClientCapabilities(sampling=SamplingCapability())
            )
        )
    except Exception:
        return False


def _connection_supports_input_required(ctx: Any) -> bool:
    """True when this connection can carry an input-required tool result.

    The multi-round-trip result type (SEP-2322) only exists on 2026-07-28+
    protocol connections; fastmcp refuses to serialize it on a handshake-era
    connection. Those connections also lost the ``ctx.sample`` back-channel in
    fastmcp 4 (#664), so a handshake-era client takes the deterministic slug
    fallback even when it advertises the ``sampling`` capability.
    """
    try:
        rc = ctx.request_context
        return rc is not None and rc.protocol_version in MODERN_PROTOCOL_VERSIONS
    except Exception:
        return False


def _sampling_available(ctx: Any) -> bool:
    """True when the two-phase sampling flow can complete on this connection."""
    return _connection_supports_input_required(ctx) and _client_supports_sampling(ctx)


def _branch_description_request(
    task_name: str, project_name: str
) -> InputRequiredResult:
    """Build the input-required leg asking the client to sample a branch suffix.

    Returned verbatim from the tool body; fastmcp wraps it so it reaches the
    client as ``resultType: "input_required"``. The client fulfills the
    ``CreateMessageRequest`` and re-invokes ``start_task`` with the result
    available under :data:`_BRANCH_DESCRIPTION_KEY` in ``ctx.input_responses``.
    """
    prompt = (
        f"Generate a git branch name suffix for this task.\n"
        f"Rules: lowercase only, hyphens instead of spaces/special chars, max 45 chars, no leading/trailing hyphens.\n"
        f"Output ONLY the suffix text, nothing else.\n"
        f"Task: {task_name}\nProject: {project_name}"
    )
    request = CreateMessageRequest(
        params=CreateMessageRequestParams(
            messages=[
                SamplingMessage(
                    role="user", content=TextContent(type="text", text=prompt)
                )
            ],
            max_tokens=30,
        )
    )
    return InputRequiredResult(input_requests={_BRANCH_DESCRIPTION_KEY: request})


def _resolve_branch_description(ctx: Any, task_name: str) -> str:
    """Return the branch-name suffix for this invocation.

    On a continuation leg the client's ``CreateMessageResult`` (keyed
    :data:`_BRANCH_DESCRIPTION_KEY` in ``ctx.input_responses``) is sanitized
    through the same :func:`_slugify` the old ``ctx.sample`` path applied.
    Everything else — non-sampling client, missing/mismatched response,
    non-text content, or sampled text that slugifies to nothing — falls back
    to the deterministic ``_slugify(task_name)``, so the ``start_task`` flow
    never hard-fails on this optional capability.
    """
    fallback = _slugify(task_name)
    try:
        responses = ctx.input_responses or {}
        response = responses.get(_BRANCH_DESCRIPTION_KEY)
    except Exception:
        return fallback
    text = getattr(getattr(response, "content", None), "text", None)
    if not isinstance(text, str):
        return fallback
    return _slugify(text.strip()) or fallback


def _env_base_branch() -> Optional[str]:
    """Return the base branch configured in the environment, or ``None`` (#903).

    An unset *or* empty/whitespace-only ``ODOO_SDK_BASE_BRANCH``
    (:data:`_BASE_BRANCH_ENV`) reads as "not configured" so an exported-but-blank
    variable falls through to the repo map and then
    :func:`_default_base_branch` instead of forking from a nameless ref.
    """
    return os.environ.get(_BASE_BRANCH_ENV, "").strip() or None


def _repo_map_projects() -> dict:
    """Return the odoo-dev repo map's ``projects`` object, or ``{}`` (#979).

    Pure read of ``$ODOO_DEV_STATE_DIR/repo-map.json`` with stdlib ``json``: the
    SDK is a *consumer* of that file and never writes it (``repo-map.sh`` owns
    every mutation, atomically and validated). Every way the read can go wrong —
    the variable unset, no file there, unreadable, invalid JSON, a ``projects``
    key that is not an object — degrades to "no map", because a base branch is a
    convenience here and a damaged map must never fail ``start_task``.
    """
    state_dir = os.environ.get(_STATE_DIR_ENV, "").strip()
    if not state_dir:
        return {}
    try:
        with open(os.path.join(state_dir, _REPO_MAP_FILE), encoding="utf-8") as handle:
            projects = json.load(handle).get("projects")
    except Exception:
        return {}
    return projects if isinstance(projects, dict) else {}


def _repo_map_entry_path(entry: Any) -> Optional[str]:
    """Real path of ``entry``'s ``repo_path``, or ``None`` when it has none."""
    path = entry.get("repo_path") if isinstance(entry, dict) else None
    return os.path.realpath(path) if isinstance(path, str) and path.strip() else None


def _repo_map_entry_for(repo_root: str) -> Optional[dict]:
    """Return the repo map's entry for ``repo_root``, or ``None`` (#979).

    The matching rule, in order:

    * an entry whose ``repo_path`` resolves (``realpath``) to ``repo_root`` — the
      unambiguous identification, and the only one that works for the several
      projects that live under a shared checkout path family such as
      ``/mnt/extra-addons``;
    * otherwise an entry whose ``repo`` (a bare folder name) equals
      ``basename(repo_root)``, **and only when exactly one entry does**. Two
      projects on one repo — an ongoing engagement and its version upgrade —
      legitimately disagree about ``default_branch``, and guessing between them
      would reintroduce the very bug this lookup exists to fix.
    """
    entries = [e for e in _repo_map_projects().values() if isinstance(e, dict)]
    by_path = [e for e in entries if _repo_map_entry_path(e) == repo_root]
    if by_path:
        return by_path[0]
    basename = os.path.basename(repo_root)
    by_name = [e for e in entries if e.get("repo") == basename]
    return by_name[0] if len(by_name) == 1 else None


def _repo_map_default_branch(repo_root: Optional[str]) -> Optional[str]:
    """Return the repo map's ``default_branch`` for this checkout, or ``None``.

    The odoo-dev repo map (``$ODOO_DEV_STATE_DIR/repo-map.json``, #979) records
    per-project where task work is based — commonly a shared pre-production
    branch such as ``E2E`` or ``UAT`` on a repo whose *GitHub* default is still
    ``master``. Reading it here is what stops ``start_task`` forking from the
    wrong branch when nobody passed a base. The checkout is identified by
    :func:`_repo_map_entry_for`, which documents the matching rule.

    :param repo_root: Real path of the git root, or ``None`` outside a repo.
    :type repo_root: Optional[str]
    :return: The recorded base branch, or ``None`` when the map is absent,
        unreadable, silent about this repo, or ambiguous about it.
    :rtype: Optional[str]
    """
    if not repo_root:
        return None
    entry = _repo_map_entry_for(repo_root)
    branch = entry.get("default_branch") if entry is not None else None
    return branch.strip() or None if isinstance(branch, str) else None


def _default_base_branch() -> Optional[_ResolvedBase]:
    """Resolve a base branch without prompting (headless automation path, #621).

    The last resort, reached only once the argument, the environment and the
    repo map have all stayed silent: it *guesses*. Prefers the remote's default
    branch (``origin/HEAD``), falling back to the branch currently checked out.
    Returns ``None`` only when neither can be determined (e.g. not a git repo,
    detached HEAD with no origin).
    """
    result = _git("symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    name = result.stdout.strip()
    if result.returncode == 0 and name:
        # ``origin/main`` -> ``main``; _resolve_base_ref re-derives the remote ref.
        return _ResolvedBase(name.split("/", 1)[-1], _SOURCE_ORIGIN_HEAD)
    current = _current_branch()
    return _ResolvedBase(current, _SOURCE_CURRENT_BRANCH) if current else None


def _on_task_branch(task_id: int) -> bool:
    """True when the working tree is already on this task's branch."""
    current = _current_branch()
    return bool(current and current.startswith(f"{task_id}-"))


def _should_request_branch_description(ctx: Any, task_id: int) -> bool:
    """True when this invocation must return the sampling input-required leg.

    Only the *first* invocation of a sampling-capable flow asks (an empty /
    absent ``ctx.input_responses`` marks it); a continuation, a non-sampling
    client, or a handshake-era connection proceeds directly. A working tree
    already on the task branch never asks: no new branch name will be minted,
    so a sampling round-trip would be pure waste. This check is read-only —
    the first leg stays side-effect free (no run row, no branch mutation) so
    the continuation can execute the full start flow exactly once. The cheap
    in-process capability gates run before the ``git`` probe so a non-sampling
    client (the common case) never pays for the extra subprocess.
    """
    if not _sampling_available(ctx):
        return False
    try:
        if ctx.input_responses:
            return False
    except Exception:
        return False
    return not _on_task_branch(task_id)


async def _elicit_base_branch(ctx: Any) -> tuple[Optional[str], Optional[str]]:
    """Ask the caller to pick a local branch to fork from; return (base, error).

    The name-search path's one remaining base prompt: it runs only when no base
    was configured (:func:`_resolve_base_branch`), and exactly one of the two
    returned values is non-``None``.

    :param ctx: FastMCP context providing ``elicit``.
    :type ctx: Any
    :return: The chosen branch, or an error string for an empty repo, a
        cancelled prompt, or an out-of-range selection.
    :rtype: tuple[Optional[str], Optional[str]]
    """
    branches = _list_local_branches()
    if not branches:
        return (
            None,
            "No local git branches found. Ensure the working directory is a git repo.",
        )

    numbered = "\n".join(f"{i + 1}. {b}" for i, b in enumerate(branches))
    result = await ctx.elicit(
        f"Select base branch to fork from:\n{numbered}\nSelect number:",
        _SelectIndex,
    )
    if result.action != "accept":
        return None, "Branch selection cancelled."
    idx = result.data.selection - 1
    if not (0 <= idx < len(branches)):
        return None, "Invalid branch selection."
    return branches[idx], None


def _configured_base_branch(base_branch: Optional[str]) -> Optional[_ResolvedBase]:
    """Return the base somebody *configured*, or ``None`` if nobody did (#979).

    The deterministic half of :func:`_resolve_base_branch`'s precedence, split
    out so that function stays a flat read of the chain: the explicit argument,
    then ``ODOO_SDK_BASE_BRANCH``, then the odoo-dev repo map's recorded
    ``default_branch`` for this checkout. None of the three guesses, so all
    three rank above both the interactive pick and ``origin/HEAD``.
    """
    if base_branch:
        return _ResolvedBase(base_branch, _SOURCE_ARGUMENT)
    env_base = _env_base_branch()
    if env_base is not None:
        return _ResolvedBase(env_base, _SOURCE_ENV)
    mapped = _repo_map_default_branch(_repo_root())
    return _ResolvedBase(mapped, _SOURCE_REPO_MAP) if mapped is not None else None


async def _resolve_base_branch(
    ctx: Any, *, interactive: bool, base_branch: Optional[str]
) -> tuple[Optional[_ResolvedBase], Optional[str]]:
    """Resolve the branch the task branch must fork from; return (base, error).

    Precedence (#903/#979), strictly: the explicit ``base_branch`` argument,
    then ``ODOO_SDK_BASE_BRANCH`` (:func:`_env_base_branch`), then the odoo-dev
    repo map's ``default_branch`` for this checkout
    (:func:`_repo_map_default_branch`) — the three *configured* answers, see
    :func:`_configured_base_branch` — and only when all three stay silent the
    fallbacks that *guess*: the interactive branch pick on the name-search
    path, or :func:`_default_base_branch` (``origin/HEAD``, then the current
    branch) on the headless ``task_id`` path (#614/#621).

    A configured base therefore also replaces the elicitation: a caller that
    already named the base — or a project whose base the repo map already
    records — has nothing to be asked, and the remote default is never even
    probed. ``origin/HEAD`` ranking *below* the map is the fix for #979: on a
    repo whose GitHub default is ``master`` while task work flows into ``E2E``,
    the remote default is exactly the wrong answer.

    :param ctx: FastMCP context, used only for the interactive fallback.
    :type ctx: Any
    :param interactive: Whether prompting the caller is allowed (name path).
    :type interactive: bool
    :param base_branch: Base named by the caller, or ``None``.
    :type base_branch: Optional[str]
    :return: The resolved base and the knob that chose it, or an error string
        when none could be found.
    :rtype: tuple[Optional[_ResolvedBase], Optional[str]]
    """
    configured = _configured_base_branch(base_branch)
    if configured is not None:
        return configured, None
    if interactive:
        picked, pick_err = await _elicit_base_branch(ctx)
        if picked is None:
            return None, pick_err
        return _ResolvedBase(picked, _SOURCE_SELECTION), None
    guessed = _default_base_branch()
    if guessed is None:
        return (
            None,
            "No base branch found. Ensure the working directory is a git repo.",
        )
    return guessed, None


async def _setup_task_branch(
    ctx: Any,
    task: dict,
    *,
    interactive: bool,
    description: str,
    base_branch: Optional[str] = None,
) -> tuple[Optional[str], bool, Optional[str], Optional[str]]:
    """Ensure the working tree sits on this task's branch; report the base used.

    The base is resolved by :func:`_resolve_base_branch` (#903/#979): explicit
    argument, then ``ODOO_SDK_BASE_BRANCH``, then the odoo-dev repo map, then a
    guess. Only the base's *name* reaches the result payload; its provenance
    travels on to :func:`_create_task_branch`, which names it in the failure
    message so a collision says which knob chose the base (#979).

    :return: ``(branch_name, created, base_branch, error)`` — ``branch_name`` is
        ``None`` when the tree was already on the task branch or setup failed,
        and ``base_branch`` names the ref actually forked from (``None`` when no
        branch was created this call).
    :rtype: tuple[Optional[str], bool, Optional[str], Optional[str]]
    """
    task_id = task["id"]
    if _on_task_branch(task_id):
        return None, False, None, None

    base, base_err = await _resolve_base_branch(
        ctx, interactive=interactive, base_branch=base_branch
    )
    if base is None:
        return None, False, None, base_err

    branch_name = f"{task_id}-{description}"

    try:
        created = _create_task_branch(branch_name, base.branch, base.source)
    except _BranchSetupError as exc:
        # Already unwound (#542): report it as an ordinary flow error so the
        # caller sees an actionable message instead of a git stack trace.
        return None, False, None, str(exc)
    return branch_name, created, base.branch, None


async def _disambiguate(
    ctx: Any,
    message: str,
    items: list,
    label: Callable[[Any], str] = lambda item: item["name"],
) -> Optional[Any]:
    """Prompt user to pick one item from a list; return item or None on cancel/bad index."""
    numbered = "\n".join(f"{i + 1}. {label(item)}" for i, item in enumerate(items))
    result = await ctx.elicit(f"{message}\n{numbered}\nSelect number:", _SelectIndex)
    if result.action != "accept":
        return None
    idx = result.data.selection - 1
    if not (0 <= idx < len(items)):
        return None
    return items[idx]


def _lookup_task_by_id(client: Any, task_id: int) -> Optional[tuple[dict, dict]]:
    """Look up a task directly by ID; return (task, project) or None if not found."""
    records = client.execute(
        "project.task",
        "search_read",
        [("id", "=", task_id)],
        fields=["id", "name", "project_id"],
        limit=1,
    )
    if not records:
        return None
    r = records[0]
    project_raw = r.get("project_id")
    project = {
        "id": project_raw[0] if isinstance(project_raw, (list, tuple)) else project_raw,
        "name": (
            project_raw[1]
            if isinstance(project_raw, (list, tuple))
            else str(project_raw)
        ),
    }
    return {"id": r["id"], "name": r["name"]}, project


async def _search_and_pick(
    ctx: Any,
    results: list,
    *,
    empty_error: str,
    multi_prompt: str,
    cancel_error: str,
) -> tuple[Optional[dict], Optional[str]]:
    """Resolve a search result to one item; return (item, error) — one non-None.

    Empty results yield ``empty_error``; a single result is returned directly; a
    multiple result set is disambiguated, with a cancel/bad-index returning
    ``cancel_error``.
    """
    if not results:
        return None, empty_error
    if len(results) == 1:
        return results[0], None
    picked = await _disambiguate(ctx, multi_prompt, results)
    if picked is None:
        return None, cancel_error
    return picked, None


async def _resolve_project(
    ctx: Any, registry: Registry, query: str
) -> tuple[Optional[dict], Optional[str]]:
    """Return (project, error_string) — exactly one will be non-None."""
    projects = registry["search_projects"].execute(query, limit=10)
    return await _search_and_pick(
        ctx,
        projects,
        empty_error=f"No projects found matching {query!r}.",
        multi_prompt="Multiple projects found:",
        cancel_error="Project selection cancelled.",
    )


async def _resolve_task(
    ctx: Any, registry: Registry, query: str, project_id: int, project_name: str
) -> tuple[Optional[dict], Optional[str]]:
    """Return (task, error_string) — exactly one will be non-None."""
    tasks = registry["search_tasks"].execute(query, project_id, limit=10)
    return await _search_and_pick(
        ctx,
        tasks,
        empty_error=f"No tasks found matching {query!r} in project {project_name!r}.",
        multi_prompt="Multiple tasks found:",
        cancel_error="Task selection cancelled.",
    )


async def _resolve_task_and_project(
    ctx: Any,
    registry: Registry,
    task_name_query: Optional[str],
    project_name_query: Optional[str],
    task_id: Optional[int],
) -> tuple[Optional[dict], Optional[dict], Optional[dict]]:
    """Resolve (task, project, error) from a task id or a name search.

    A supplied ``task_id`` is authoritative (#614): it is looked up directly and
    NO name search runs — an unknown id is an error, never a fuzzy fallback that
    could land on the wrong task. Only the name-query path searches and may
    elicit disambiguation.
    """
    if task_id is not None:
        client = registry["search_projects"]._client
        found = _lookup_task_by_id(client, task_id)
        if found is None:
            return None, None, {"error": f"Task {task_id} not found."}
        task, project = found
        return task, project, None

    project, err = await _resolve_project(ctx, registry, project_name_query or "")
    if err:
        return None, None, {"error": err}
    task, err = await _resolve_task(
        ctx, registry, task_name_query or "", project["id"], project["name"]
    )
    if err:
        return None, None, {"error": err}
    return task, project, None


def _missing_selector_error(
    task_id: Optional[int], task_name_query: Optional[str]
) -> Optional[dict[str, Any]]:
    """Return the selector error dict when neither selector was supplied (#614)."""
    if task_id is None and not task_name_query:
        return {"error": "Provide task_id or task_name_query."}
    return None


def _running_run(db, task_id: int):
    """Return the task's session iff it is already RUNNING, else ``None``.

    Pre-flight for the #621 dispatch, kept out of the tool body so the
    already-running short-circuit reads as one condition there.
    """
    active = db.get_active_run(task_id)
    if active is not None and getattr(active, "state", None) is TaskState.RUNNING:
        return active
    return None


@composition_tool("start_task")
def make_start_task_tool(registry: Registry):
    """Build the async ``start_task`` MCP tool bound to ``registry``.

    :param registry: Command registry providing search + start commands.
    :type registry: Registry
    :return: Async callable implementing the ``start_task`` tool.
    """

    async def start_task(
        ctx: Context,
        task_name_query: Optional[str] = None,
        project_name_query: Optional[str] = None,
        task_id: Optional[int] = None,
        base_branch: Optional[str] = None,
    ) -> Union[dict[str, Any], InputRequiredResult]:
        """Idempotently ensure a RUNNING tracking session on an Odoo project.task.

        THE single lifecycle entry point (start / ensure / resume) — safe to
        call from automation in any session state. Dispatches on the task's
        current session state BEFORE any side effect: already RUNNING -> no-op
        success returning the existing run with already_running=true (no branch
        mutation, no prompts); AWAITING_ANSWERS -> transitions back to RUNNING;
        STOPPED (non-aborted) -> resumes the stopped run in place; aborted /
        CLOSED / no session -> creates a new run. resume_task is a thin alias
        for the resume transitions; stop_task moves an active session to
        STOPPED.

        task_id alone is sufficient and authoritative: it is looked up directly
        with zero name searches and zero elicitation prompts (headless-safe),
        and a git task branch (<task-id>-<slug>) is set up when one is needed.
        Without task_id, searches by task_name_query (and optional
        project_name_query) with disambiguation prompts. Writes no Odoo
        timesheet and posts no chatter note (hours are derived by the
        sessionization upload path).

        base_branch names the branch to fork the task branch from. Precedence:
        this argument, then ODOO_SDK_BASE_BRANCH, then the odoo-dev repo map's
        default_branch for this checkout ($ODOO_DEV_STATE_DIR/repo-map.json,
        #979), then the remote default branch (origin/HEAD), then the current
        branch. Pass it on any project whose base is not the GitHub default and
        is not in the repo map (#903) — the created branch is forked from
        origin/<base_branch> and its upstream is set there, and the resolved
        base comes back in the result.
        """
        selector_error = _missing_selector_error(task_id, task_name_query)
        if selector_error is not None:
            return selector_error

        task, project, error = await _resolve_task_and_project(
            ctx, registry, task_name_query, project_name_query, task_id
        )
        if error is not None:
            return error

        # Pre-flight state check (#621) BEFORE any side effect: an already-
        # RUNNING session short-circuits to the command's no-op result with no
        # branch setup and no elicitation. Every other state (AWAITING_ANSWERS,
        # resumable STOPPED, aborted/CLOSED/none) proceeds to branch setup and
        # the command's idempotent dispatch.
        db = registry["start_task"].state
        if _running_run(db, task["id"]) is not None:
            return registry["start_task"].execute(
                task_id=task["id"],
                task_name=task["name"],
                project_id=project["id"],
                project_name=project["name"],
            )

        # Two-phase sampling (SEP-2322, #664): a sampling-capable flow's first
        # invocation returns the input-required leg here — after resolution and
        # the RUNNING fastpath, before ANY mutation — so the continuation
        # re-runs the same read-only steps and executes the start flow exactly
        # once. Non-sampling clients skip straight to the deterministic slug.
        if _should_request_branch_description(ctx, task["id"]):
            return _branch_description_request(task["name"], project["name"])
        description = _resolve_branch_description(ctx, task["name"])

        original_branch = _current_branch()
        branch_name: Optional[str] = None
        branch_created = False

        try:
            # Branch setup lives *inside* the rollback scope (#541): running it
            # outside meant a failure between ``checkout -b`` and the auto-stash
            # pop left the user on a dangling task branch with no cleanup.
            (
                branch_name,
                branch_created,
                branch_base,
                branch_err,
            ) = await _setup_task_branch(
                ctx,
                task,
                interactive=task_id is None,
                description=description,
                base_branch=base_branch,
            )
            if branch_err:
                return {"error": branch_err}

            return registry["start_task"].execute(
                task_id=task["id"],
                task_name=task["name"],
                project_id=project["id"],
                project_name=project["name"],
                branch_name=branch_name,
                base_branch=branch_base,
            )
        except Exception:
            # Raise-based error contract (#223): the start command raises on
            # failure (e.g. an Odoo fault -> ``OdooError``), as does git when a
            # checkout is impossible (``CalledProcessError``). This flow needs
            # cleanup before the failure surfaces, so it catches to roll back
            # only a branch freshly created this run (#164), then re-raises the
            # *original typed* exception unchanged for the MCP
            # ``_error_boundary`` (#222) to format — it is not swallowed into an
            # ``{"error": ...}`` dict here.
            _rollback_task_branch_if_created(
                branch_name, branch_created, original_branch
            )
            raise

    return start_task
