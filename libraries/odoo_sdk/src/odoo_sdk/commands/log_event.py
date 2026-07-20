import subprocess
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from .command import Command
from .protocols import RpcClient
from odoo_sdk.state import LocalConfig, LocalStateClient, current_repo_label
from odoo_sdk.state.models import EventRecord


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
    :func:`~odoo_sdk.state.db.current_repo_label`, and best-effort in the same
    way: a detached HEAD or a non-repo cwd yields ``""`` rather than a bogus
    branch name.

    ``symbolic-ref`` is asked rather than ``rev-parse --abbrev-ref`` (the form
    :mod:`odoo_sdk.mcp.tools.start_task` uses to *compare* branches) because it
    answers the question this path actually asks. It fails outright on a
    detached HEAD instead of printing the literal ``HEAD`` as if that were a
    branch, and it still names the branch on a repo with no commits yet, where
    ``rev-parse`` cannot resolve the revision at all.
    """

    return _git_text("symbolic-ref", "--short", "HEAD")


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


class LogEventCommand(Command):
    """Append one row to the local ``events`` timeseries via the command layer.

    This is the single command-layer owner of the ``events`` append write. Two
    frontends share it so that "commands own state mutation" holds for event
    emission too (issue #407):

    * the CLI ``log-event`` subcommand, which persists the ``claude:<hook>`` shim
      events, and
    * the MCP server's dispatch-event emission, which records exactly one
      ``source="agent"`` telemetry row per successful tool call.

    Each frontend resolves its own interface-specific inputs — the source string,
    subject, payload, and timestamp — and hands them here; the command owns the
    :class:`~odoo_sdk.state.models.EventRecord` construction, the
    :meth:`~odoo_sdk.state.LocalStateClient.add_event` write, and — since #507 /
    #509 — the two policies every frontend used to re-implement:

    * **Task attribution** (:meth:`resolve_task_ids`). A caller passes the
      attribution *hint* its interface happens to expose (``--task-id`` from the
      CLI, a bound ``task_id`` argument from the MCP dispatch wrapper), not a
      resolved scope. When the hint names no task the command falls back to the
      active runs, because an event that lands with an empty ``task_ids`` is
      excluded from session derivation permanently and can therefore never bill.
    * **Provenance** (``repo`` / ``branch``). Resolved from the working tree at
      write time unless the caller states them explicitly, so no frontend can
      write an event that silently loses the code it came from.

    Unlike the SDK's tool-backing commands it is **not** a ``@builtin_command``:
    the built-in surface is a bijection with the MCP tool surface (enforced by
    ``test_every_builtin_command_has_an_explicit_tool``), and event emission must
    never be an LLM-callable tool. It is therefore a plain shared command that
    both frontends import and construct directly.

    The command performs no Odoo RPC, so its injected ``client`` is unused and
    defaults to ``None``: a caller with no RPC client (the local-only CLI
    ``log-event`` path and the MCP telemetry wrapper) can construct it as
    ``LogEventCommand(state=db)``.
    """

    _name = "log_event"
    _description = (
        "Append one row to the local events timeseries. Records the source, "
        "subject, payload, task scope, and repo/branch/PR provenance of a single "
        "hook/dispatch event; performs no Odoo RPC."
    )

    def __init__(
        self,
        client: Optional[RpcClient] = None,
        state: Optional[LocalStateClient] = None,
        config: Optional[LocalConfig] = None,
    ):
        """Bind the command, defaulting ``client`` to ``None``.

        The event-append write never touches Odoo, so the RPC client is optional
        here (unlike the base :class:`~odoo_sdk.commands.command.Command`, which
        requires one). This lets a client-less frontend construct the command as
        ``LogEventCommand(state=db)``.

        :param client: RPC client (unused); defaults to ``None``.
        :type client: Optional[RpcClient]
        :param state: Shared local state client, resolved lazily when ``None``.
        :type state: Optional[LocalStateClient]
        :param config: Shared resolved SDK config, resolved lazily when ``None``.
        :type config: Optional[LocalConfig]
        """

        super().__init__(client, state=state, config=config)

    def _active_run_task_ids(self) -> list[str]:
        """Return the task ids of every non-stale active run, as id strings.

        Stale runs are excluded (#366): a run whose last activity predates the
        reap threshold (``ODOO_REAP_THRESHOLD_HOURS`` env, default
        :data:`~odoo_sdk.reap.DEFAULT_REAP_THRESHOLD_HOURS`) is a wedged orphan
        from a dead devcontainer, so attaching an event to it would only accrue
        phantom billable wall-clock; skipping it freezes its activity clock so it
        stays reapable. The same staleness predicate ``reap`` uses is applied
        here, so the two agree on exactly which runs are stale.

        :mod:`odoo_sdk.reap` is imported inside the method rather than at module
        scope: it reaches ``odoo_sdk.billing``, which imports the partially
        initialized ``odoo_sdk`` package that is itself importing this module.
        """

        from odoo_sdk.reap import (
            is_run_stale,
            resolve_env_threshold_hours,
            threshold_from_hours,
        )

        threshold = threshold_from_hours(resolve_env_threshold_hours())
        return [
            str(run.task_id)
            for run in self.state.get_all_active_runs()
            if not is_run_stale(self.state, run, threshold)
        ]

    def resolve_task_ids(
        self,
        task_ids: Optional[Iterable[Any]] = None,
        attach_active_run: bool = True,
    ) -> list[str]:
        """Resolve which tasks an event attributes to — the one attribution rule.

        Attribution used to be decided independently at each write site, keyed on
        whatever that interface happened to expose: the MCP wrapper attributed an
        event only when the dispatched tool's signature contained a parameter
        literally named ``task_id``, so two tools doing equivalent work on the
        same task landed on opposite sides of the derivation filter (#507). The
        policy is now stated once, here:

        1. An explicit hint wins. ``task_ids`` is normalized by
           :func:`normalize_task_ids`, so a caller may hand over whatever its
           interface produced without pre-validating it.
        2. Otherwise the event attributes to every non-stale active run, because
           all interaction with a task — read-only inspection included — is
           active work on it.
        3. With no active run (or with ``attach_active_run`` disabled) the scope
           is empty: untargeted session-level activity.

        :param task_ids: Explicit attribution hint; non-task values are dropped.
        :type task_ids: Optional[Iterable[Any]]
        :param attach_active_run: Whether to fall back to the active runs when
            the hint names no task. The CLI passes its ``--attach-active-run``
            flag through here to preserve the documented hook-shim contract.
        :type attach_active_run: bool
        :return: The task ids the event attributes to, as id strings.
        :rtype: list[str]
        """

        explicit = normalize_task_ids(task_ids)
        if explicit:
            return explicit
        if not attach_active_run:
            return []
        return self._active_run_task_ids()

    def execute(
        self,
        source: str,
        subject: str = "",
        payload: Optional[dict[str, Any]] = None,
        task_ids: Optional[Iterable[Any]] = None,
        repo: Optional[str] = None,
        branch: Optional[str] = None,
        pr_num: int = 0,
        external_id: Optional[str] = None,
        timestamp: Optional[datetime] = None,
        attach_active_run: bool = True,
    ) -> dict[str, Any]:
        """Persist one event row and return a summary of what was written.

        ``source``, ``subject``, ``payload``, ``pr_num``, and ``external_id`` are
        written verbatim. The three fields the command owns policy for are
        resolved here: the task scope via :meth:`resolve_task_ids`, ``repo`` and
        ``branch`` from the working tree when the caller leaves them unstated,
        and the ``timestamp`` default of the current UTC time.

        :param source: Event source string (e.g. ``claude:PostToolUse`` from the
            CLI shim, or ``agent`` from the MCP dispatch wrapper); persisted
            verbatim.
        :type source: str
        :param subject: Human-readable event subject.
        :type subject: str
        :param payload: Optional JSON object of extra fields; ``None`` writes no
            payload.
        :type payload: Optional[dict[str, Any]]
        :param task_ids: Explicit attribution hint; see :meth:`resolve_task_ids`
            for how it combines with the active runs.
        :type task_ids: Optional[Iterable[Any]]
        :param repo: Repository label the event originated from; ``None``
            resolves it from the cwd's git remote, ``""`` records none.
        :type repo: Optional[str]
        :param branch: Branch the event originated from; ``None`` resolves it
            from the cwd's checked-out HEAD, ``""`` records none.
        :type branch: Optional[str]
        :param pr_num: Pull-request number the event belongs to, ``0`` for none.
            Never inferred: identifying the PR for a branch costs a forge API
            round trip, which does not belong on a per-tool-call write path, so a
            caller that knows the number states it.
        :type pr_num: int
        :param external_id: Stable external identity for idempotent ingestion
            (``git:<sha>``, ``gh:pr:<n>``, ...); ``None`` for events with no
            external origin, which never dedupe.
        :type external_id: Optional[str]
        :param timestamp: Event time; defaults to the current UTC time when
            ``None``.
        :type timestamp: Optional[datetime]
        :param attach_active_run: Whether an unhinted event falls back to the
            active runs; see :meth:`resolve_task_ids`.
        :type attach_active_run: bool
        :return: ``{"source", "subject", "task_ids"}`` describing the written row.
        :rtype: dict[str, Any]
        """

        record = EventRecord(
            id=None,
            source=source,
            timestamp=timestamp or datetime.now(timezone.utc),
            task_ids=self.resolve_task_ids(task_ids, attach_active_run),
            repo=current_repo_label() if repo is None else repo,
            branch=current_branch_label() if branch is None else branch,
            pr_num=pr_num,
            subject=subject,
            payload=payload,
            external_id=external_id,
        )
        self.state.add_event(record)
        return {
            "source": record.source,
            "subject": record.subject,
            "task_ids": record.task_ids,
        }
