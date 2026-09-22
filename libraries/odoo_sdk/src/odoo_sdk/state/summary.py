"""Machine-derived run/session summaries from recorded events and notes (#626).

Pure helpers — no I/O, no state — that turn a run's (or derived session's)
recorded :class:`~odoo_sdk.state.models.EventRecord` rows and its locally-stored
notes into a single-line, reconstructable narrative of what happened: the tools
that ran, the commits (short sha + subject line), the branch/PR provenance, the
recorded test result, and the checkpoint notes. ``stop_task`` stores the result
on the run row (``task_runs.run_summary``) and the billing upload attaches it to
the session's timesheet entry, so detail capture is fully automatic — never a
human gate or elicitation (#623).

:func:`summarize_session_context` (#710) is the narrative counterpart: the same
events read for what was DONE rather than for how many tools ran. The billing
upload leads a timesheet name with it and demotes the tally to a trailing debug
suffix, because a row named ``actions: Bash x231, Edit x50, …`` — or, with no
run on record at all, ``[/] session 32459|37297`` — tells a reviewer nothing
they can bill from.

Length policy (maintainer decision, #626): derived run summaries — like event
payloads and timesheet names — are internal/local text and carry NO length
limit. The 300-character cap (``enforce_chatter_body_limit``) applies ONLY to
chatter bodies posted to Odoo (``task_note`` / ``task_question``) and must never
be applied here. The one bound that does exist is per-ITEM and applies only to
the #710 headline (:data:`_HEADLINE_ITEM_CHARS`): the tally, the commits, and
the notes still appear in full in the tail.
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

#: Join between the chatter notes of :func:`summarize_session_context`'s first
#: tier, distinct from :data:`_ITEM_JOIN` because a note's own first line often
#: contains commas and would otherwise read as several items.
_NOTE_JOIN = " | "

#: Length of the abbreviated commit sha included in the commit segment.
_SHORT_SHA_CHARS = 9

#: Per-item cap for the billing HEADLINE only (#710). The no-length-cap policy
#: in the module docstring governs the derived summary as a whole and is
#: unchanged: the debug tail still carries every tally, commit, and note in
#: full. What this bounds is one headline item — a chatter note whose body is
#: multi-KB markdown, or a user prompt — because the headline's entire job is to
#: be readable at a glance on a timesheet row, and a multi-KB "first line" is
#: exactly the unreadable narrative #710 was filed about. Truncated items are
#: marked with :data:`_TRUNCATION_MARK` so a reader can tell.
_HEADLINE_ITEM_CHARS = 120
_TRUNCATION_MARK = "..."

#: Sources whose events carry a forge/VCS narrative (tier 2). ``comment`` is the
#: review-family alias the resync puller writes for an authored PR/issue comment
#: (see ``adapters.state.persistence._SOURCE_ALIASES``), so it is summarised
#: beside ``review`` rather than being silently dropped.
_REVIEW_SOURCES = ("review", "comment")

#: Payload key the resync puller records a submitted review's state under
#: (``APPROVED`` / ``CHANGES_REQUESTED`` / ``COMMENTED`` / ``DISMISSED``).
_REVIEW_STATE_KEY = "review_state"

#: Payload keys the ``claude-event-hook`` shim records the session's context
#: under (#710): the truncated first user prompt at ``UserPromptSubmit``, the
#: session's authoritative working directory, and the transcript jsonl a richer
#: summariser can read later.
_PROMPT_KEY = "prompt"
_CWD_KEY = "cwd"


def _one_line(text: str) -> str:
    """Collapse all whitespace runs so ``text`` reads as a single line."""
    return re.sub(r"\s+", " ", text).strip()


def _headline_item(text: str) -> str:
    """Return ``text``'s FIRST line, collapsed and capped for the headline.

    Only the first line is kept — the rest of a checkpoint note or a pasted
    prompt is body, not headline — and the result is capped at
    :data:`_HEADLINE_ITEM_CHARS`. Returns ``""`` for text with no content.
    """
    first = _one_line(text.split("\n", 1)[0]) if text else ""
    if len(first) <= _HEADLINE_ITEM_CHARS:
        return first
    return first[:_HEADLINE_ITEM_CHARS].rstrip() + _TRUNCATION_MARK


def _distinct(items: list[str]) -> list[str]:
    """Return the non-empty members of ``items`` in first-seen order, deduped."""
    return list(dict.fromkeys(item for item in items if item))


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


def _payload_of(event: EventRecord) -> dict:
    """Return ``event``'s payload as a dict (``{}`` when absent or malformed)."""
    payload = event.payload
    return payload if isinstance(payload, dict) else {}


def _chatter_headline(events: list[EventRecord]) -> str:
    """Tier 1 (#710): the first line of each chatter note in the window.

    Chatter notes are the user's OWN words about the task and are therefore the
    best billing narrative available; they beat every machine-derived tier. The
    note text comes from the event ``subject``, which the resync puller fills
    from the Odoo message subject, falling back to the first line of the
    message body (the usual case — a logged note carries no subject).
    """
    return _NOTE_JOIN.join(
        _distinct(
            [
                _headline_item(event.subject)
                for event in events
                if event.source == "chatter"
            ]
        )
    )


def _forge_item(event: EventRecord) -> str:
    """Render one commit / PR / review event as a narrative item, or ``""``."""
    pr = f"PR #{event.pr_num}" if event.pr_num else "PR"
    title = _headline_item(event.subject)
    if event.source == "commit":
        return f"commit: {title}" if title else ""
    if event.source == "pr_opened":
        return f"{pr} opened: {title}" if title else f"{pr} opened"
    if event.source == "merge":
        return f"{pr} merged: {title}" if title else f"{pr} merged"
    if event.source == "comment":
        return f"{pr} commented"
    state = _one_line(str(_payload_of(event).get(_REVIEW_STATE_KEY) or ""))
    return f"{pr} reviewed ({state})" if state else f"{pr} reviewed"


def _forge_headline(events: list[EventRecord]) -> str:
    """Tier 2 (#710): commit subjects, PR titles, and review states in order.

    Everything this reads — ``commit``/``pr_opened``/``merge``/``review``
    ``subject`` (the commit subject line or the PR title), ``pr_num``, and the
    review ``state`` the puller records in the payload — is already persisted
    on the event row, so this tier needs no new capture to work on history that
    has already been resynced.
    """
    sources = ("commit", "pr_opened", "merge", *_REVIEW_SOURCES)
    return _ITEM_JOIN.join(
        _distinct([_forge_item(event) for event in events if event.source in sources])
    )


def _hook_headline(events: list[EventRecord]) -> str:
    """Tier 3 (#710): the session's own context, for hook-only sessions.

    A session made only of ``claude:<Hook>`` rows has no commit, no PR, and no
    chatter to speak for it — the case that produced the bare
    ``[/] session <task>|<event>`` names. The shim now records the truncated
    first user prompt and the session's ``cwd`` in the event payload, so the
    prompt (what the user actually asked for) names the session, and the
    working directory names it when no prompt was captured.
    """
    payloads = [_payload_of(event) for event in events]
    prompts = _distinct(
        [_headline_item(str(p.get(_PROMPT_KEY) or "")) for p in payloads]
    )
    if prompts:
        return f"prompt: {prompts[0]}"
    cwds = _distinct([_one_line(str(p.get(_CWD_KEY) or "")) for p in payloads])
    return f"cwd {_ITEM_JOIN.join(cwds)}" if cwds else ""


def summarize_session_context(events: list[EventRecord]) -> str:
    """Derive the billing HEADLINE for a session from its events (#710).

    The narrative half of a timesheet name, separate from
    :func:`summarize_run_activity`, which stays the machine tally. Timesheet
    rows used to be named by that tally (``actions: Bash x231, Edit x50, …``)
    or, with no run at all, by the bare session key — neither of which tells a
    reviewer what was done. This returns the first non-empty tier of the
    preference order #710 states:

    1. **Chatter notes** the user authored on the task inside the window.
    2. **Commits, PR titles, and review states** recorded in the window.
    3. **Hook context** — the first user prompt, else the session's ``cwd``.

    Strict preference, not concatenation: the highest tier present is the most
    human, most specific account of the session, and stacking the lower ones
    behind it only re-creates the unreadable line this replaces. Whatever the
    caller uses as the tally still belongs on the row — as a trailing debug
    suffix, which :func:`odoo_sdk.billing.upload._derived_description` appends.

    Returns ``""`` when no tier has anything to say, so the caller applies its
    own fallback.
    """
    return (
        _chatter_headline(events) or _forge_headline(events) or _hook_headline(events)
    )


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
