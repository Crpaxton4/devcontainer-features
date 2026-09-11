"""Shared dispatch boundary + telemetry for the command-dispatching surfaces.

Extracted from ``odoo_sdk.mcp.server`` (#713) so the CLI's generic ``odoo-sdk
cmd`` dispatcher and the MCP tool surface share ONE error vocabulary and ONE
event emitter instead of two drifting copies. This module is core-layer on
purpose (ADR-005): it imports nothing from any surface (``cli`` / ``mcp`` /
``tui``), so both surfaces can import it while staying mutually independent.
``odoo_sdk.mcp.server`` re-imports every name below under its original module
attribute, so existing patch targets (``odoo_sdk.mcp.server._emit_tool_event``
and friends) keep working unchanged.

Two concerns live here, both properties of *dispatching a command* rather than
of any one transport:

* the **error boundary vocabulary** — :data:`_BOUNDARY_ERRORS` and
  :func:`_error_payload`, which define which exceptions are caller-actionable
  and the single ``{"error": {"type", "message"}}`` envelope they render as;
* the **dispatch telemetry** — :func:`_emit_tool_event` and its helpers, which
  append the one ``source="agent"`` event a successful dispatch records.
"""

import subprocess
from typing import Any, Optional, Tuple

from odoo_sdk.errors import OdooError
from odoo_sdk.tracking.models import (
    TaskAlreadyRunningError,
    TaskNotRunningError,
    TrackerStateMissingError,
)

from .log_event import LogEventCommand, normalize_task_ids

#: Exceptions the dispatch error boundary renders as a structured payload.
#: Every entry is a caller-actionable failure — a classified Odoo fault
#: (:class:`~odoo_sdk.transport.errors.OdooError` and its subclasses), a
#: session-state violation, an absent host-provisioned tracker DB, a failed git
#: invocation from the ``start_task`` branch setup
#: (``subprocess.CalledProcessError``, #541 — it used to escape as a stack
#: trace), or invalid input (``ValueError``) — a shape an LLM can reason about
#: and retry. Anything not listed (``KeyError``, ``AttributeError``, ...) is a
#: programming error and is deliberately left to propagate as an unhandled
#: traceback. Shared verbatim by the MCP error boundary and the CLI ``cmd``
#: dispatcher (#713) so both surfaces classify failures identically.
_BOUNDARY_ERRORS: Tuple[type[BaseException], ...] = (
    OdooError,
    TaskNotRunningError,
    TaskAlreadyRunningError,
    TrackerStateMissingError,
    subprocess.CalledProcessError,
    ValueError,
)


def _error_payload(exc: BaseException) -> dict[str, dict[str, str]]:
    """Render a caught exception as the uniform structured-error payload.

    The concrete class name is used (rather than the caught base) so a mapped
    subclass such as ``OdooValidationError`` remains distinguishable to callers.
    """

    return {"error": {"type": type(exc).__name__, "message": str(exc)}}


def _event_task_ids(arguments: dict[str, Any]) -> list[str]:
    """Return the explicit attribution *hint* a call's bound arguments carry.

    A tool that takes an int-coercible ``task_id`` names the task its event
    belongs to; every other tool yields no hint. This is deliberately only a
    hint, not the task scope: a tool signature that omits ``task_id`` says
    nothing about whether work is happening on a task, and treating it as an
    empty scope is what left inspection-only tool calls permanently unbillable
    (#507). :meth:`~odoo_sdk.commands.log_event.LogEventCommand.resolve_task_ids`
    owns what an unhinted event actually attributes to.

    :param arguments: Bound tool arguments (``ctx`` already excluded).
    :type arguments: dict[str, Any]
    :return: ``[str(task_id)]`` when present and int-coercible, else ``[]``.
    :rtype: list[str]
    """

    return normalize_task_ids([arguments.get("task_id")])


#: Identifier-shaped result fields lifted into the agent-event payload (#626).
#: Every key names an identifier, provenance marker, or machine-derived outcome
#: (``run_summary`` is the automatic narrative ``stop_task`` computes) — never a
#: caller-supplied free-text input. Payloads are internal/local text with NO
#: length limit; the 300-character cap (``enforce_chatter_body_limit``) applies
#: only to chatter bodies posted to Odoo.
_RESULT_PAYLOAD_KEYS = (
    "run_id",
    "task_id",
    "state",
    "elapsed",
    "elapsed_hours",
    "branch_name",
    "pr_url",
    "pr_num",
    "commit_sha",
    "test_result",
    "timesheet_id",
    "message_id",
    "run_summary",
)


def _result_payload_fields(result: Any) -> dict[str, Any]:
    """Lift the allowlisted identifier/outcome fields from a tool result (#626).

    Only scalar values are taken so the payload stays a flat, queryable record;
    absent and ``None`` fields are simply omitted. Anything outside the
    allowlist — chatter bodies, task descriptions, search hits — never reaches
    the local events store.
    """
    if not isinstance(result, dict):
        return {}
    return {
        key: result[key]
        for key in _RESULT_PAYLOAD_KEYS
        if isinstance(result.get(key), (str, int, float, bool))
    }


def _outcome_line(result: Any) -> str:
    """One-line outcome of a dispatch: ``"ok"`` or the error the tool reported.

    The event wrapper only fires on a *successful* dispatch (an exception
    propagates past the emit), but a tool may still hand back a structured
    ``{"error": ...}`` payload — e.g. a declined elicitation — which is an
    outcome worth auditing distinctly from a plain success.
    """
    if isinstance(result, dict) and "error" in result:
        error = result["error"]
        message = error.get("message", "") if isinstance(error, dict) else str(error)
        return f"error: {message}" if message else "error"
    return "ok"


def _emit_tool_event(
    state: Any,
    name: str,
    arguments: dict[str, Any],
    result: Any = None,
    via: Optional[str] = None,
) -> None:
    """Append one ``source="agent"`` event describing a successful dispatch.

    The write is routed through :class:`~odoo_sdk.commands.log_event.
    LogEventCommand` — the single command-layer owner of the ``events`` append
    (issue #407) — rather than constructing an ``EventRecord`` and calling
    ``add_event`` inline, so "commands own state mutation" holds for dispatch
    telemetry too. The command is bound directly to ``state`` (resolved from
    ``registry.state_client`` at call time), never looked up by name, so
    emission does not depend on ``log_event`` being registered on the caller's
    registry.

    The persisted payload is reconstructable (#626) without leaking free text:
    the tool name, the call's argument *names* (shape, not content — the safe
    middle ground the event-record policy review recommended for #510), a
    one-line ``outcome``, and the allowlisted identifier fields lifted from the
    result (:data:`_RESULT_PAYLOAD_KEYS` — run/branch/PR/test identifiers and
    the machine-derived ``run_summary``). Chatter note bodies, stakeholder
    questions, search queries, and other free-text *inputs* are still never
    written to the local events store, matching the ``claude-event-hook`` shim's
    stance of recording tool identifiers without prompt/``tool_input``
    contents. What is *sent to Odoo* is unaffected; this concerns only local
    persistence.

    Neither the task scope nor the repo/branch provenance is decided here. The
    bound ``task_id`` (when the tool has one) is handed over as an attribution
    *hint* and the command applies the shared policy — falling back to the active
    runs for a tool that takes no ``task_id``, so inspecting a task is attributed
    to it exactly like mutating one (#507). ``repo`` and ``branch`` are likewise
    left unstated so the command resolves them from the working tree; this path
    used to hardcode ``repo=""``, which left every agent event unattributable to
    the code that produced it and sessionized it under the repo-less sentinel
    (#509).

    :param state: The local state store the event is appended to.
    :type state: Any
    :param name: Public tool name.
    :type name: str
    :param arguments: Bound tool arguments (``ctx`` already excluded); used to
        derive the task scope and the argument-*name* list, never persisted as
        values.
    :type arguments: dict[str, Any]
    :param result: The tool's (post-``on_result``) return value; only the
        allowlisted identifier fields and a one-line outcome are persisted.
    :type result: Any
    :param via: Dispatch surface marker recorded in the payload (#713) — the
        CLI ``cmd`` dispatcher passes ``"cli"``. ``None`` (the MCP dispatch
        wrapper) records no marker, keeping the historical payload shape.
    :type via: Optional[str]
    :return: None.
    :rtype: None
    """

    payload: dict[str, Any] = {
        "tool": name,
        "args": sorted(arguments),
        "outcome": _outcome_line(result),
        **({"via": via} if via is not None else {}),
        **_result_payload_fields(result),
    }
    LogEventCommand(state=state).execute(
        source="agent",
        subject=name,
        payload=payload,
        task_ids=_event_task_ids(arguments),
    )
