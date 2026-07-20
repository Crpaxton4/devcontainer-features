from datetime import datetime, timezone
from typing import Any, Optional

from .command import Command
from .protocols import RpcClient
from odoo_sdk.state import LocalConfig, LocalStateClient
from odoo_sdk.state.models import EventRecord


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
    subject, payload, task scope, repo label, and timestamp — and hands them
    here; the command owns only the
    :class:`~odoo_sdk.state.models.EventRecord` construction and the
    :meth:`~odoo_sdk.state.LocalStateClient.add_event` write.

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
        "subject, payload, task scope, and repo of a single hook/dispatch event; "
        "performs no Odoo RPC."
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

    def execute(
        self,
        source: str,
        subject: str = "",
        payload: Optional[dict[str, Any]] = None,
        task_ids: Optional[list[str]] = None,
        repo: str = "",
        timestamp: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """Persist one event row and return a summary of what was written.

        The record's ``source``, ``subject``, ``payload``, ``task_ids``, and
        ``repo`` are written verbatim so both frontends keep their exact
        semantics; only the ``timestamp`` default (the current UTC time when the
        caller passes ``None``) is applied here.

        :param source: Event source string (e.g. ``claude:PostToolUse`` from the
            CLI shim, or ``agent`` from the MCP dispatch wrapper); persisted
            verbatim.
        :type source: str
        :param subject: Human-readable event subject.
        :type subject: str
        :param payload: Optional JSON object of extra fields; ``None`` writes no
            payload.
        :type payload: Optional[dict[str, Any]]
        :param task_ids: Task ids the event attributes to; ``None`` records an
            empty (untargeted) scope.
        :type task_ids: Optional[list[str]]
        :param repo: Repository label the event originated from.
        :type repo: str
        :param timestamp: Event time; defaults to the current UTC time when
            ``None``.
        :type timestamp: Optional[datetime]
        :return: ``{"source", "subject", "task_ids"}`` describing the written row.
        :rtype: dict[str, Any]
        """

        record = EventRecord(
            id=None,
            source=source,
            timestamp=timestamp or datetime.now(timezone.utc),
            task_ids=list(task_ids) if task_ids is not None else [],
            repo=repo,
            subject=subject,
            payload=payload,
        )
        self.state.add_event(record)
        return {
            "source": record.source,
            "subject": record.subject,
            "task_ids": record.task_ids,
        }
