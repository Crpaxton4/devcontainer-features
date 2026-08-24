"""Machine-derived run/session summaries from recorded events and notes (#626).

Pure helpers — no I/O, no state — that turn a run's (or derived session's)
recorded :class:`~odoo_sdk.state.models.EventRecord` rows and its locally-stored
notes into a single-line, reconstructable narrative of what happened: the tools
that ran, the commits (short sha + subject line), the branch/PR provenance, the
recorded test result, and the checkpoint notes. ``stop_task`` stores the result
on the run row (``task_runs.run_summary``) and the billing upload attaches it to
the session's timesheet entry, so detail capture is fully automatic — never a
human gate or elicitation (#623).

Length policy (maintainer decision, #626): derived run summaries — like event
payloads and timesheet names — are internal/local text and carry NO length
limit. The 300-character cap (``enforce_chatter_body_limit``) applies ONLY to
chatter bodies posted to Odoo (``task_note`` / ``task_question``) and must never
be applied here.
"""

from __future__ import annotations

import re
from collections import Counter

from .models import EventRecord

#: Joins between the top-level summary segments (actions / commits / provenance
#: / notes) and between the items inside one segment, kept distinct so a segment
#: containing several items still reads unambiguously on one line.
_SEGMENT_JOIN = "; "
_ITEM_JOIN = ", "

#: Length of the abbreviated commit sha included in the commit segment.
_SHORT_SHA_CHARS = 9


def _one_line(text: str) -> str:
    """Collapse all whitespace runs so ``text`` reads as a single line."""
    return re.sub(r"\s+", " ", text).strip()


def _action_segment(events: list[EventRecord]) -> str:
    """Tally the agent/hook tool activity, e.g. ``actions: task_note x3, ...``.

    Agent events carry the tool name as ``subject``; ``claude:<Hook>`` shim
    events may carry an empty subject, in which case the source names the
    action. Ordered by frequency so the dominant activity leads the line.
    """
    counts = Counter(
        event.subject or event.source
        for event in events
        if event.source == "agent" or event.source.startswith("claude:")
    )
    if not counts:
        return ""
    listed = _ITEM_JOIN.join(
        f"{name} x{count}" if count > 1 else name
        for name, count in counts.most_common()
    )
    return f"actions: {listed}"


def _commit_segment(events: list[EventRecord]) -> str:
    """List the run's commits as ``<short-sha> <subject line>`` items.

    The sha comes from the resync puller's ``git:<sha>`` external id when
    present; the subject is the commit's subject line, collapsed to one line.
    """
    items = []
    for event in events:
        if event.source != "commit":
            continue
        external_id = event.external_id or ""
        sha = external_id.removeprefix("git:")[:_SHORT_SHA_CHARS]
        item = " ".join(part for part in (sha, _one_line(event.subject)) if part)
        if item:
            items.append(item)
    return f"commits: {_ITEM_JOIN.join(items)}" if items else ""


def _provenance_segments(events: list[EventRecord]) -> list[str]:
    """Branch / PR / test-result segments recovered from the events.

    A PR URL recorded in any event payload wins over the bare ``pr_num``; the
    LAST recorded ``test_result`` wins (it reflects the run's final state).
    """
    segments = []
    branches = list(dict.fromkeys(e.branch for e in events if e.branch))
    if branches:
        segments.append(f"branch {_ITEM_JOIN.join(branches)}")
    pr_urls = [
        e.payload["pr_url"]
        for e in events
        if isinstance(e.payload, dict) and e.payload.get("pr_url")
    ]
    pr_num = max((e.pr_num for e in events), default=0)
    if pr_urls:
        segments.append(f"PR {pr_urls[-1]}")
    elif pr_num:
        segments.append(f"PR #{pr_num}")
    test_results = [
        e.payload["test_result"]
        for e in events
        if isinstance(e.payload, dict) and e.payload.get("test_result")
    ]
    if test_results:
        segments.append(f"tests: {test_results[-1]}")
    return segments


def _notes_segment(notes: list[str]) -> str:
    """Fold the run's checkpoint notes into one segment, each on one line."""
    flattened = [_one_line(note) for note in notes if _one_line(note)]
    return f"notes: {' | '.join(flattened)}" if flattened else ""


def summarize_run_activity(events: list[EventRecord], notes: list[str]) -> str:
    """Derive a one-line narrative of a run/session from its events and notes.

    The single derivation both consumers share (#626): ``stop_task`` feeds it
    the run window's events plus the run's notes and stores the result on the
    run row, and the billing upload feeds it a derived session's events to name
    the timesheet entry. Returns ``""`` when there is nothing to tell (no
    events, no notes) so callers can apply their own fallback. No length cap —
    see the module docstring for the policy.
    """
    segments = [
        _action_segment(events),
        _commit_segment(events),
        *_provenance_segments(events),
        _notes_segment(notes),
    ]
    return _SEGMENT_JOIN.join(segment for segment in segments if segment)
