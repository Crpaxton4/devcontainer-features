"""The ten dead-letter causes of design section 18, and how a session ending picks one.

The cause names where in its own sequence the dispatcher was when things
stopped. No upstream status string is parsed to get it. Status fields (subtype,
``is_error``, ``terminal_reason``) go into the free-text detail, where they help
a human and cannot mislead a branch.

Nothing is salvaged: no partial result is reconstructed, no description is
synthesised, nothing is retried.
"""

import json
from dataclasses import dataclass

from odoo_client.state.jobs import DeadReason

from .schema import usable_structured

#: Each cause as a complete statement, for a row read at speed.
STATEMENTS: dict[DeadReason, str] = {
    DeadReason.DUPLICATE_TASK_IN_FLIGHT: (
        "Rejected at the gate: the task already had a live job. Nothing ran."
    ),
    DeadReason.CONTAINER_START_FAILED: (
        "Bringing the container up did not succeed, or it did not become ready "
        "(postgres and odoo answering) in time. No session ever existed."
    ),
    DeadReason.WORKSPACE_MOUNT_INVALID: (
        "The container came up and the mount assertion found the wrong thing "
        "at the workspace or the odoo-dev mount."
    ),
    DeadReason.SESSION_LAUNCH_FAILED: (
        "The session never started: bad flags, a missing binary, or a "
        "non-zero exit before any output."
    ),
    DeadReason.KILLED_BY_DEADLINE: (
        "The wall-clock guard fired and the dispatcher killed the session."
    ),
    DeadReason.KILLED_BY_SIGNAL: (
        "Something other than the dispatcher's deadline killed the session."
    ),
    DeadReason.SESSION_EXIT_NONZERO: (
        "The session ran and exited non-zero with no usable result object."
    ),
    DeadReason.STRUCTURED_OUTPUT_MISSING: (
        "A result object arrived without a usable structured value."
    ),
    DeadReason.STRUCTURED_OUTPUT_UNREADABLE: (
        "Output arrived and could not be parsed as a result object."
    ),
    DeadReason.ORPHANED_BY_RESTART: (
        "A stale running record found at startup. Its process died with a "
        "previous dispatcher."
    ),
}

#: Envelope keys copied into the detail, when present.
_ENVELOPE_DETAIL_KEYS = (
    "subtype",
    "is_error",
    "terminal_reason",
    "errors",
    "api_error_status",
    "num_turns",
)


@dataclass(frozen=True)
class Ending:
    """What the dispatcher saw when a session process ended."""

    returncode: int | None
    deadline_hit: bool = False
    saw_output: bool = False
    envelope: dict | None = None
    unreadable: bool = False
    parsed_any: bool = False
    stderr_tail: str = ""


def describe(reason: DeadReason, facts: dict | None = None, tail: str = "") -> str:
    """The detail text: the cause as a statement, then everything the dispatcher saw."""
    parts = [STATEMENTS[reason]]
    if facts:
        parts.append(
            "; ".join(f"{key}={_render(value)}" for key, value in facts.items())
        )
    if tail:
        parts.append("stderr tail: " + tail)
    return " ".join(parts)


def _render(value) -> str:
    return value if isinstance(value, str) else json.dumps(value)


def _facts(ending: Ending) -> dict:
    facts: dict = {"exit_code": ending.returncode}
    if ending.returncode is not None and ending.returncode < 0:
        facts["signal"] = -ending.returncode
    for key in _ENVELOPE_DETAIL_KEYS:
        if ending.envelope and key in ending.envelope:
            facts[key] = ending.envelope[key]
    return facts


def classify(ending: Ending) -> tuple[DeadReason, str] | None:
    """Return ``(cause, detail)`` for a failed ending, or None for a success.

    The success test is whether a usable structured value arrived, and nothing
    else. Order after that: our own kill, someone else's kill, then what the
    output looked like.
    """
    if usable_structured(ending.envelope) is not None:
        return None
    reason = _cause(ending)
    return reason, describe(reason, _facts(ending), ending.stderr_tail)


def _cause(ending: Ending) -> DeadReason:
    if ending.deadline_hit:
        return DeadReason.KILLED_BY_DEADLINE
    if ending.returncode is not None and ending.returncode < 0:
        return DeadReason.KILLED_BY_SIGNAL
    if ending.envelope is not None:
        return DeadReason.STRUCTURED_OUTPUT_MISSING
    if ending.unreadable:
        return DeadReason.STRUCTURED_OUTPUT_UNREADABLE
    if not ending.saw_output:
        return DeadReason.SESSION_LAUNCH_FAILED
    if ending.returncode:
        return DeadReason.SESSION_EXIT_NONZERO
    # Exit 0 with output but no result object: valid events with nothing to
    # read are MISSING; output that never parsed at all is UNREADABLE.
    if ending.parsed_any:
        return DeadReason.STRUCTURED_OUTPUT_MISSING
    return DeadReason.STRUCTURED_OUTPUT_UNREADABLE
