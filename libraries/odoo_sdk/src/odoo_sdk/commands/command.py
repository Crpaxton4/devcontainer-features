from abc import ABC, abstractmethod
from typing import Any, Optional

from odoo_sdk.state import (
    LocalConfig,
    LocalStateClient,
    TaskRun,
)

from .protocols import RpcClient

#: Maximum characters allowed in a chatter message/note body posted by the SDK
#: (#610). Enforced at the command layer — transport-agnostic per ADR-004 — so
#: every consumer (MCP tools, CLI, library callers) inherits the same cap.
MAX_CHATTER_BODY_CHARS = 300


def enforce_chatter_body_limit(body: str, label: str) -> None:
    """Reject a chatter ``body`` over :data:`MAX_CHATTER_BODY_CHARS` (#610).

    Shared by the chatter-posting builtin commands (``task_note``,
    ``task_question``). Over-limit content is *rejected* with a
    :class:`ValueError` telling the caller to shorten it — never silently
    truncated, which would post content the caller did not write.

    :param body: The caller-supplied message text to validate.
    :param label: Parameter name used in the error message (e.g. ``"note"``).
    """
    if len(body) > MAX_CHATTER_BODY_CHARS:
        raise ValueError(
            f"{label} is {len(body)} characters, over the "
            f"{MAX_CHATTER_BODY_CHARS}-character limit for posted chatter "
            "content. Shorten the message: keep notes simple, direct, plain."
        )


def require_active_run(db: LocalStateClient, task_id: int) -> TaskRun:
    """Return the active run for ``task_id`` or raise ``TaskNotRunningError``.

    Shared by the session-mutating builtin commands (``task_question``,
    ``task_note``, ``abort_task``, ``stop_task``). A thin delegate to
    :meth:`LocalStateClient.require_active_run` — the ONE guard implementation
    and message (#627) — kept here so the command layer retains a single,
    stable import site for the precondition.
    """
    return db.require_active_run(task_id)


class Command(ABC):
    """Base interface for all Odoo SDK Commands.

    A command is an atomic, composable unit of business logic with a single
    ``execute`` entry point. Commands never reference interaction surfaces (MCP,
    CLI) and never reference each other; shared logic lives in ``utilities``.

    Commands receive three peer dependencies, injected by the :class:`Registry`:

    * ``client`` — any :class:`RpcClient` (the :class:`OdooClient` in
      production; a structural fake in tests).
    * ``state`` — the :class:`LocalStateClient` (SQLite session FSM).
    * ``config`` — the :class:`LocalConfig` (resolved SDK settings).

    The client is required; the :class:`Registry` always injects it. The state
    and config dependencies are created lazily on first access so that
    lightweight commands (and unit tests) that only need the client are not
    forced to construct SQLite state or read a config file.
    """

    _name: str
    _description: str
    _client: RpcClient

    def __init__(
        self,
        client: RpcClient,
        state: Optional[LocalStateClient] = None,
        config: Optional[LocalConfig] = None,
    ):
        self._client = client
        self._injected_state = state
        self._injected_config = config

    @property
    def state(self) -> LocalStateClient:
        """Return the injected local state client, creating one on first use."""
        if self._injected_state is None:
            self._injected_state = LocalStateClient()
        return self._injected_state

    @property
    def config(self) -> LocalConfig:
        """Return the injected local config, resolving one on first use."""
        if self._injected_config is None:
            self._injected_config = LocalConfig.load()
        return self._injected_config

    @abstractmethod
    def execute(self, *args: Any, **kwargs: Any) -> Any: ...

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description
