"""Shared dispatch boundary + telemetry for the command-dispatching surfaces.

Extracted from ``odoo_client.mcp.server`` (#713) so the CLI's generic ``odoo-sdk
cmd`` dispatcher and the MCP tool surface share ONE error vocabulary and ONE
event emitter instead of two drifting copies. This module is core-layer on
purpose (ADR-005): it imports nothing from any surface (``cli`` / ``mcp`` /
``tui``), so both surfaces can import it while staying mutually independent.
``odoo_client.mcp.server`` re-imports every name below under its original module
attribute, so existing patch targets (``odoo_client.mcp.server._emit_tool_event``
and friends) keep working unchanged.

Two concerns live here, both properties of *dispatching a command* rather than
of any one transport:

* the **error boundary vocabulary** — :data:`_BOUNDARY_ERRORS` and
  :func:`_error_payload`, which define which exceptions are caller-actionable
  and the single ``{"error": {"type", "message"}}`` envelope they render as;
* the **dispatch telemetry** — :func:`_emit_tool_event` and its helpers, which
  append the one ``source="agent"`` event a successful dispatch records.
"""

import os
import subprocess
from typing import Any, Optional, Tuple

from odoo_client.errors import OdooError
from odoo_client.tracking.models import (
    TaskAlreadyRunningError,
    TaskNotRunningError,
    TrackerStateMissingError,
)

from .log_event import LogEventCommand, normalize_task_ids

#: Exceptions the dispatch error boundary renders as a structured payload.
#: Every entry is a caller-actionable failure — a classified Odoo fault
#: (:class:`~odoo_client.transport.errors.OdooError` and its subclasses), a
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


#: Tool names whose bound ``task_id`` is an attribution *hint* (#1040).
#:
#: Every entry MUTATES the task it names — it writes to the Odoo record (chatter
#: note/question, a new task, an activity) or to the tracker run FSM (start /
#: stop / resume / close / abort). Naming a task to such a tool is itself work on
#: that task, so the event it emits attributes to it.
#:
#: Every other built-in is deliberately absent, which is the whole point. A tool
#: that only READS a task — ``get_task``, ``get_task_chatter``,
#: ``get_task_attachments``, ``get_todo``, ``query_sessions``, every ``search_*``
#: / ``task_*`` report — used to tag its queried id, so one investigation read
#: became a derived session and the ``min_session_hours`` floor turned it into a
#: 0.25 h timesheet (#1040: 55 of 90 sessions in one window were read-only).
#: The queried id is still recorded, as :data:`READ_TASK_IDS_PAYLOAD_KEY`
#: evidence, so the read remains auditable without being billable.
#:
#: ``assign_event`` and ``normalize_timesheets`` are likewise absent on purpose:
#: they write, but what they write IS the billing record, so attributing them to
#: their target task would bill the act of correcting that task's time — the
#: self-perpetuating loop #1040 reports from retagging.
#:
#: Reads still attribute to an OPEN run through
#: :meth:`~odoo_client.commands.log_event.LogEventCommand.resolve_task_ids` rule 2
#: (#507): inspecting task B while a run on task A is open is time spent on A.
#: What this set removes is only the *hint* — the claim that the queried id is
#: the task being worked.
#:
#: Pinned by ``tests/test_mcp/test_server_events.py``, which enumerates every
#: built-in command and asserts each one is classified exactly once, so a new
#: command cannot silently default into either behaviour.
ATTRIBUTING_TOOLS: frozenset[str] = frozenset(
    {
        # Tracker run FSM writes.
        "start_task",
        "stop_task",
        "resume_task",
        "close_task",
        "abort_task",
        "abort_run",
        "stop_run",
        "stop_all",
        # Odoo record writes.
        "task_note",
        "task_question",
        "create_task",
        "schedule_activity",
        "mark_activity_done",
    }
)

#: Payload key carrying the task id a NON-attributing (read) tool queried
#: (#1040). Evidence only: it is deliberately NOT in the row's ``task_ids``, so
#: session derivation, upload, and every report never see it, while the TUI
#: review surface can still show that the event was a read (see
#: :mod:`odoo_client.tui.evidence`, which pins this spelling).
READ_TASK_IDS_PAYLOAD_KEY = "read_task_ids"


def _bound_task_id(arguments: dict[str, Any]) -> list[str]:
    """Return the int-coercible ``task_id`` a call's bound arguments name.

    :param arguments: Bound tool arguments (``ctx`` already excluded).
    :type arguments: dict[str, Any]
    :return: ``[str(task_id)]`` when present and int-coercible, else ``[]``.
    :rtype: list[str]
    """

    return normalize_task_ids([arguments.get("task_id")])


def _event_task_ids(name: str, arguments: dict[str, Any]) -> list[str]:
    """Return the explicit attribution *hint* a call carries, or ``[]``.

    A tool in :data:`ATTRIBUTING_TOOLS` that takes an int-coercible ``task_id``
    names the task its event belongs to; every other tool yields no hint, even
    when it took a ``task_id`` — reading a task is not a claim that the task was
    worked (#1040), and the queried id is recorded as
    :data:`READ_TASK_IDS_PAYLOAD_KEY` evidence by :func:`_queried_task_ids`
    instead.

    This is deliberately only a hint, not the task scope: an unhinted event is
    still attributed from context, so an inspection-only call made inside an open
    run remains billable to that run rather than permanently unbillable (#507).
    :meth:`~odoo_client.commands.log_event.LogEventCommand.resolve_task_ids` owns
    what an unhinted event actually attributes to.

    :param name: Public tool name, matched against :data:`ATTRIBUTING_TOOLS`.
    :type name: str
    :param arguments: Bound tool arguments (``ctx`` already excluded).
    :type arguments: dict[str, Any]
    :return: ``[str(task_id)]`` for an attributing tool, else ``[]``.
    :rtype: list[str]
    """

    return _bound_task_id(arguments) if name in ATTRIBUTING_TOOLS else []


def _queried_task_ids(name: str, arguments: dict[str, Any]) -> list[str]:
    """Return the task id a non-attributing (read) tool merely QUERIED (#1040).

    The exact complement of :func:`_event_task_ids`: an attributing tool records
    nothing here (its id is real attribution, already in ``task_ids``), and a
    read tool records the id it looked at as payload evidence.

    :param name: Public tool name, matched against :data:`ATTRIBUTING_TOOLS`.
    :type name: str
    :param arguments: Bound tool arguments (``ctx`` already excluded).
    :type arguments: dict[str, Any]
    :return: ``[str(task_id)]`` for a read tool, else ``[]``.
    :rtype: list[str]
    """

    return [] if name in ATTRIBUTING_TOOLS else _bound_task_id(arguments)


#: Identifier-shaped result fields lifted into the agent-event payload (#626).
#: Every key names an identifier, provenance marker, or machine-derived outcome
#: (``run_summary`` is the automatic narrative ``stop_task`` computes) — never a
#: caller-supplied free-text input. Payloads are internal/local text with NO
#: length limit; the 500-character cap (``enforce_chatter_body_limit``) applies
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


#: Set to ``1`` by the dispatcher on every container session it starts. Such a
#: session is measured by the run record (#1065), so its tool calls must not
#: also emit attributing events.
UNATTENDED_ENV = "ODOO_UNATTENDED"


def _is_dispatched_run() -> bool:
    """Return whether this process is inside a dispatched, unattended run.

    Any non-empty value counts, including ``0``: the odoo-dev guard hook uses the
    same rule, so the guard and the billing evidence never disagree.
    """
    return os.environ.get(UNATTENDED_ENV, "") != ""


def _emit_tool_event(
    state: Any,
    name: str,
    arguments: dict[str, Any],
    result: Any = None,
    via: Optional[str] = None,
) -> None:
    """Append one ``source="agent"`` event describing a successful dispatch.

    The write is routed through :class:`~odoo_client.commands.log_event.
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
    bound ``task_id`` of a MUTATING tool (:data:`ATTRIBUTING_TOOLS`) is handed
    over as an attribution *hint* and the command applies the shared policy —
    falling back to the active runs for an unhinted event, so a tool call made
    inside an open run is attributed to that run rather than left permanently
    unbillable (#507). A READ tool's queried id is NOT a hint (#1040): it rides
    in the payload under :data:`READ_TASK_IDS_PAYLOAD_KEY` as evidence, so
    investigating a task no longer mints a billable session on it. ``repo`` and
    ``branch`` are likewise left unstated so the command resolves them from the
    working tree; this path used to hardcode ``repo=""``, which left every agent
    event unattributable to the code that produced it and sessionized it under
    the repo-less sentinel (#509).

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

    queried = _queried_task_ids(name, arguments)
    payload: dict[str, Any] = {
        "tool": name,
        "args": sorted(arguments),
        "outcome": _outcome_line(result),
        **({"via": via} if via is not None else {}),
        **({READ_TASK_IDS_PAYLOAD_KEY: queried} if queried else {}),
        **_result_payload_fields(result),
    }
    LogEventCommand(state=state).execute(
        source="agent",
        subject=name,
        payload=payload,
        task_ids=_event_task_ids(name, arguments),
    )
