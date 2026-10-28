"""GitHub puller: authored PRs, reviews and comments across the whole account.

``sync_github`` searches the account (``gh search prs --author``) instead of the
current repo and stores ``pr_opened``, ``merge``, ``review`` and ``comment``
events. It is idempotent: every event carries a stable ``external_id``.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Callable, Optional

from odoo_dev_data.adapters._common import (
    _extract_task_ids,
    _parse_iso_utc,
    _run_capture,
    _store_pending,
    _Window,
    _window_bounds,
)
from odoo_dev_data.config import LocalConfig
from odoo_dev_data.events import EventRecord, EventStore


def _gh_login() -> Optional[str]:
    """Return the authenticated GitHub login, or None when gh is unusable."""
    return _run_capture(["gh", "api", "user", "--jq", ".login"]) or None


def _gh_json(cmd: list[str]) -> Optional[Any]:
    """Run a gh command and JSON-decode its stdout, or None when unusable."""
    out = _run_capture(cmd)
    if out is None:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def _github_logins(config: Optional[LocalConfig], active: str) -> list[str]:
    """Return the GitHub logins to capture for (issue #378 item 4).

    The configured non-email identities; falls back to the authenticated
    ``active`` login so a single-account user needs no config.
    """
    configured = [a for a in (config.resync_authors if config else []) if "@" not in a]
    return configured or [active]


def _review_login(actor: dict) -> str:
    """Return the login of a review/comment's author, or empty when absent."""
    return (actor.get("user") or {}).get("login", "")


def _repo_of(item: dict) -> str:
    """Return a gh-search result's ``owner/repo`` slug, or empty string."""
    return (item.get("repository") or {}).get("nameWithOwner", "")


def _ts_in_window(
    ts_str: Optional[str], since: datetime, until: datetime
) -> Optional[datetime]:
    """Parse an ISO timestamp; return it when ``since <= ts < until``, else None."""
    if not ts_str:
        return None
    try:
        parsed = _parse_iso_utc(ts_str)
    except ValueError:
        return None
    return parsed if since <= parsed < until else None


# Fixed caps on the account-wide gh searches. gh refuses to run unbounded, so
# truncation is possible; :func:`_truncation_warnings` detects a full-to-the-cap
# result and surfaces it rather than letting ``found`` masquerade as complete.
_AUTHORED_SEARCH_LIMIT = 200
_FAMILY_SEARCH_LIMIT = 100


def _gh_authored_prs(login: str, window: _Window) -> Optional[list[dict]]:
    """Return PRs ``login`` authored across ALL repos, active in the window (#652).

    Account-wide ``gh search prs --author`` replaces the repo-scoped
    ``gh pr list``, so the same PRs are captured wherever resync runs. The
    server-side bound is ``--updated`` (like the sibling reviewed-by/commenter
    searches), NOT ``--created``: a PR created before the window but merged
    inside it was updated by that merge, so it is still returned and its merge
    event can mint — a created-bounded search would silently lose it. The
    search JSON carries no ``headRefName``/``mergedAt`` — those come per PR
    from :func:`_gh_pr_detail`.
    """
    return _gh_json(
        [
            "gh",
            "search",
            "prs",
            "--author",
            login,
            "--updated",
            f">={window.since.date().isoformat()}",
            "--limit",
            str(_AUTHORED_SEARCH_LIMIT),
            "--json",
            "number,title,repository,state,createdAt,updatedAt",
        ]
    )


def _gh_pr_detail(slug: str, number: int) -> dict:
    """Fetch one PR's ``headRefName``/``mergedAt`` — fields the search JSON lacks.

    Degrades to ``{}`` on failure so the event is still minted (branch-less),
    mirroring :func:`_collect_review_family`'s per-parent tolerance: one
    unreadable PR never drops the rest of the capture.
    """
    return (
        _gh_json(
            [
                "gh",
                "pr",
                "view",
                str(number),
                "-R",
                slug,
                "--json",
                "headRefName,mergedAt",
            ]
        )
        or {}
    )


def _pr_opened_event(pr: dict, window: _Window) -> Optional[EventRecord]:
    """Build a billable ``pr_opened`` event for one authored PR, or None (#656).

    Opening a PR is review-family work (assembling the diff, writing the
    description), so it derives windowed sessions exactly like a submitted
    review. ``pr`` is a search item already enriched with its
    :func:`_gh_pr_detail` fields. Timestamped at ``createdAt``; skipped when the
    item names no repo or the timestamp is missing/out of window. The external
    id is repo-qualified like the merge event's (``gh:pr:<owner/repo>:<n>``)
    with an ``:opened`` suffix — the qualification is mandatory now that PR
    numbers collide across an account-wide search (#652), and the suffix keeps
    the shape distinct from the merge row so a historical re-resync inserts
    opened events even for PRs already known as merges.
    """
    repo = _repo_of(pr)
    if not repo:
        return None
    ts = _ts_in_window(pr.get("createdAt"), window.since, window.until)
    if ts is None:
        return None
    number = pr["number"]
    title = pr.get("title", "")
    branch = pr.get("headRefName", "")
    return EventRecord(
        id=None,
        source="pr_opened",
        timestamp=ts,
        task_ids=_extract_task_ids(title, branch, allow_leading_id=True),
        repo=repo,
        pr_num=number,
        branch=branch,
        subject=title,
        external_id=f"gh:pr:{repo}:{number}:opened",
    )


def _pr_merged_event(pr: dict, window: _Window) -> Optional[EventRecord]:
    """Build a ``merge`` (audit-only) event for one MERGED authored PR, or None.

    ``pr`` is a search item already enriched with its :func:`_gh_pr_detail`
    fields. Only minted when ``mergedAt`` is set, and timestamped there; skipped
    when the item names no repo or ``mergedAt`` falls outside the window — a PR
    opened inside the window but merged after it is still captured, by
    :func:`_pr_opened_event` at its creation time (#656). The external id is
    repo-qualified (``gh:pr:<owner/repo>:<n>``) — mandatory now that PR numbers
    collide across an account-wide search (#652).
    """
    repo = _repo_of(pr)
    if not repo:
        return None
    ts = _ts_in_window(pr.get("mergedAt"), window.since, window.until)
    if ts is None:
        return None
    number = pr["number"]
    title = pr.get("title", "")
    branch = pr.get("headRefName", "")
    return EventRecord(
        id=None,
        source="merge",
        timestamp=ts,
        task_ids=_extract_task_ids(title, branch, allow_leading_id=True),
        repo=repo,
        pr_num=number,
        branch=branch,
        subject=title,
        external_id=f"gh:pr:{repo}:{number}",
    )


def _review_event(
    review: dict, pr: dict, repo: str, window: _Window
) -> Optional[EventRecord]:
    """Build a ``review`` event for one submitted review, or None (out of window).

    ``pr`` must arrive detail-enriched: the gh search JSON carries no
    ``headRefName``, and without the real branch every branch-encoded task id
    was lost, leaving reviews structurally unbillable (#653).

    The review's ``state`` (``APPROVED`` / ``CHANGES_REQUESTED`` / ``COMMENTED``
    / ``DISMISSED``) is recorded in the payload (#710) so the billing narrative
    can say ``PR #n reviewed (APPROVED)`` rather than naming a session that says
    nothing about which PR was reviewed or what the verdict was. It goes in the
    payload — an existing free-form JSON column — rather than a new column, so
    no schema migration is involved. A review with no state records no payload.
    """
    ts = _ts_in_window(review.get("submitted_at"), window.since, window.until)
    if ts is None:
        return None
    branch = pr.get("headRefName", "")
    title = pr.get("title", "")
    state = review.get("state") or ""
    return EventRecord(
        id=None,
        source="review",
        timestamp=ts,
        task_ids=_extract_task_ids(title, branch, allow_leading_id=True),
        repo=repo,
        pr_num=pr.get("number", 0),
        branch=branch,
        subject=title,
        payload={"review_state": state} if state else None,
        external_id=f"gh:review:{review['id']}",
    )


def _comment_event(
    comment: dict, item: dict, repo: str, window: _Window
) -> Optional[EventRecord]:
    """Build a ``comment`` event for one authored issue/PR comment, or None.

    ``comment`` (issue #378 item 3) is keyed ``gh:comment:<id>`` and derives as
    windowed sessions. Skipped outside the window.
    """
    ts = _ts_in_window(comment.get("created_at"), window.since, window.until)
    if ts is None:
        return None
    title = item.get("title", "")
    return EventRecord(
        id=None,
        source="comment",
        timestamp=ts,
        task_ids=_extract_task_ids(title, "", allow_leading_id=True),
        repo=repo,
        pr_num=item.get("number", 0),
        subject=title,
        external_id=f"gh:comment:{comment['id']}",
    )


# One collector for the three review-family sources (issue #378 item 3): fetch a
# parent's sub-objects (reviews or comments), keep those authored by ``login``,
# and build an event for each. ``parents`` yields ``(parent, repo, api_slug)``.
def _collect_review_family(
    parents: list[tuple[dict, str, str]],
    login: str,
    window: _Window,
    api_path: Callable[[str, int], str],
    builder: Callable[[dict, dict, str, _Window], Optional[EventRecord]],
) -> list[EventRecord]:
    events: list[EventRecord] = []
    for parent, repo, api_slug in parents:
        # --paginate: an unpaginated gh api returns only the FIRST page (30,
        # oldest first), silently dropping the newest — i.e. in-window —
        # reviews/comments on long threads (#653).
        subs = (
            _gh_json(["gh", "api", "--paginate", api_path(api_slug, parent["number"])])
            or []
        )
        events.extend(
            event
            for sub in subs
            if _review_login(sub) == login
            if (event := builder(sub, parent, repo, window)) is not None
        )
    return events


def _reviews_path(slug: str, number: int) -> str:
    return f"repos/{slug}/pulls/{number}/reviews"


def _own_review_events(
    window: _Window, login: str, prs: list[dict]
) -> list[EventRecord]:
    """Return ``login``'s reviews on their OWN authored PRs (issue #378 #3).

    ``prs`` arrive already detail-enriched (see :func:`_enrich_authored_prs`),
    so each review event reads its PR's real ``headRefName`` and is stored
    against the PR's own repo (#652/#653).
    """
    parents = [(pr, repo, repo) for pr in prs if (repo := _repo_of(pr))]
    return _collect_review_family(parents, login, window, _reviews_path, _review_event)


def _gh_reviewed_prs(login: str, window: _Window) -> list[dict]:
    """Return PRs (on ANY repo) that ``login`` submitted a review on (issue #378 #3).

    ``--updated`` bounds the search server-side (#653): a PR reviewed in-window
    was necessarily updated at/after the review, so the filter can lose nothing
    that :func:`_review_event`'s window check would have kept.
    """
    return (
        _gh_json(
            [
                "gh",
                "search",
                "prs",
                "--reviewed-by",
                login,
                "--updated",
                f">={window.since.date().isoformat()}",
                "--limit",
                str(_FAMILY_SEARCH_LIMIT),
                "--json",
                "number,title,repository",
            ]
        )
        or []
    )


def _others_review_events(
    window: _Window, login: str, reviewed: list[dict], authored: set[tuple[str, int]]
) -> list[EventRecord]:
    """Return ``login``'s reviews on OTHER people's PRs across repos (issue #378 #3).

    ``authored`` (the identity's own ``(repo, number)`` PRs) is excluded — the
    reviewed-by search has no author filter, so without it a self-review on an
    own PR would be collected here AND by :func:`_own_review_events`, double-
    fetching and double-reporting one review. Each remaining parent is
    detail-enriched before the events build, restoring the real ``headRefName``
    the search JSON lacks so branch-encoded task ids attribute again (#653).
    Each review is stored against the reviewed PR's own repo.
    """
    parents = [
        ({**item, **_gh_pr_detail(repo, item["number"])}, repo, repo)
        for item in reviewed
        if (repo := _repo_of(item)) and (repo, item["number"]) not in authored
    ]
    return _collect_review_family(parents, login, window, _reviews_path, _review_event)


def _gh_commented_issues(login: str, window: _Window) -> list[dict]:
    """Return issues/PRs (on ANY repo) that ``login`` authored a comment on.

    ``--updated`` bounds the search server-side, mirroring
    :func:`_gh_reviewed_prs` (a commented item was updated by the comment).
    """
    return (
        _gh_json(
            [
                "gh",
                "search",
                "issues",
                "--commenter",
                login,
                "--updated",
                f">={window.since.date().isoformat()}",
                "--limit",
                str(_FAMILY_SEARCH_LIMIT),
                "--json",
                "number,title,repository",
            ]
        )
        or []
    )


def _comment_events(
    window: _Window, login: str, commented: list[dict]
) -> list[EventRecord]:
    """Return ``login``'s authored issue/PR comments across repos (issue #378 #3)."""
    parents = [(item, repo, repo) for item in commented if (repo := _repo_of(item))]
    return _collect_review_family(
        parents,
        login,
        window,
        lambda slug, number: f"repos/{slug}/issues/{number}/comments",
        _comment_event,
    )


def _enrich_authored_prs(prs: list[dict]) -> list[dict]:
    """Merge each authored PR's :func:`_gh_pr_detail` fields into its search item.

    The merged dicts feed BOTH the merge-event build and own-review collection,
    so every downstream ``pr.get("headRefName")``/``pr.get("mergedAt")`` reads
    the real value the search JSON lacks (#653). A repo-less item is passed
    through unenriched and skipped later by :func:`_pr_event`.
    """
    return [
        {**pr, **(_gh_pr_detail(repo, pr["number"]) if (repo := _repo_of(pr)) else {})}
        for pr in prs
    ]


def _truncation_warnings(counts: list[tuple[str, int, int]]) -> list[str]:
    """Flag any account-wide search that filled its fixed cap (#652).

    A result exactly at the limit is best-match ordered and very likely
    clipped, so ``found`` would silently under-report; the warning tells the
    user to narrow the window instead of trusting the counters.
    """
    return [
        f"{name} search hit its {limit}-item cap; results may be truncated "
        "(narrow the window with --start/--end)"
        for name, count, limit in counts
        if count >= limit
    ]


def _github_identity_events(
    window: _Window, login: str
) -> Optional[tuple[list[EventRecord], list[str]]]:
    """Collect one identity's events plus warnings, or None on hard fail.

    Returns None only when the authored-PR search itself cannot be read (the one
    condition that fails the whole puller); the other collectors degrade to
    empty lists so one unreachable search never drops the other sources. The
    second element carries search-truncation warnings.

    Each authored PR yields up to TWO events (#656): a billable ``pr_opened``
    event at ``createdAt`` and — merged PRs only — an audit ``merge`` event at
    ``mergedAt``. Side benefit: an unmerged PR no longer mints the merge id at
    open time, so for PRs first captured after this change the later merge event
    is no longer blocked by an already-inserted open-time row — the
    stale-merge-timestamp limitation is fixed going forward.
    """
    prs = _gh_authored_prs(login, window)
    if prs is None:
        return None
    enriched = _enrich_authored_prs(prs)
    authored = {(repo, pr["number"]) for pr in enriched if (repo := _repo_of(pr))}
    reviewed = _gh_reviewed_prs(login, window)
    commented = _gh_commented_issues(login, window)
    events: list[EventRecord] = [
        event
        for pr in enriched
        for builder in (_pr_opened_event, _pr_merged_event)
        if (event := builder(pr, window)) is not None
    ]
    events.extend(_own_review_events(window, login, enriched))
    events.extend(_others_review_events(window, login, reviewed, authored))
    events.extend(_comment_events(window, login, commented))
    warnings = _truncation_warnings(
        [
            (f"authored-PR ({login})", len(prs), _AUTHORED_SEARCH_LIMIT),
            (f"reviewed-PR ({login})", len(reviewed), _FAMILY_SEARCH_LIMIT),
            (f"commented-issue ({login})", len(commented), _FAMILY_SEARCH_LIMIT),
        ]
    )
    return events, warnings


def _unattributed_reviews(inserted: list[EventRecord]) -> list[str]:
    """Label the NEWLY stored review events that carry no billable task id (#653).

    Scoped to first-sight rows on purpose: an already-stored review may have
    been attributed in triage since, so re-warning about it on every resync
    would train the user to ignore the signal.
    """
    return [
        f"{event.repo}#{event.pr_num}"
        for event in inserted
        if event.source == "review" and not event.task_ids
    ]


def sync_github(
    state: EventStore,
    config: Optional[LocalConfig] = None,
    client: Any = None,
    *,
    now: Optional[datetime] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> dict[str, Any]:
    """Reconcile authored GitHub activity account-wide (#378, #652, #653).

    For every configured author identity (default the active login) and bounded
    by the resolved window, stores: every authored PR across every repo (via
    ``gh search prs --author``) as a billable ``pr_opened`` event at creation
    time keyed ``gh:pr:<owner/repo>:<n>:opened`` plus — merged PRs only — a
    ``merge`` audit event keyed ``gh:pr:<owner/repo>:<n>`` (#656); the user's
    reviews on their own AND others' PRs as ``review`` events, each carrying
    its PR's real ``headRefName`` fetched per PR (#653); and authored issue/PR
    comments as ``comment`` events. Task ids are validated in one batch when
    ``client`` is supplied. Idempotent. Unusable/unauthenticated gh and a
    failed authored-PR search are hard ``{"error": reason}`` results; newly
    stored review events that resolved no task id are reported under
    ``unattributed_reviews``, and a search that filled its fixed cap under
    ``warnings``.
    """
    login = _gh_login()
    if login is None:
        return {"error": "gh unavailable or not authenticated"}
    try:
        window = _Window(*_window_bounds(config, now, start, end))
    except ValueError as exc:
        return {"error": str(exc)}
    pending: list[EventRecord] = []
    warnings: list[str] = []
    for identity in _github_logins(config, login):
        collected = _github_identity_events(window, identity)
        if collected is None:
            return {"error": "gh search prs failed"}
        identity_events, identity_warnings = collected
        pending.extend(identity_events)
        warnings.extend(identity_warnings)
    inserted = _store_pending(state, pending, client)
    result: dict[str, Any] = {"inserted": len(inserted), "found": len(pending)}
    unattributed = _unattributed_reviews(inserted)
    if unattributed:
        result["unattributed_reviews"] = unattributed
    if warnings:
        result["warnings"] = warnings
    return result
