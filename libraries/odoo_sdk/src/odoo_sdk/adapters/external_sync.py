"""Idempotent resync pullers reconciling local event state with external history.

Three small, current-repo-scoped pullers write into the unified ``events`` table
so that sessions — derived from events at query time — reflect work that happened
outside the live hook/agent stream: local git commits, merged GitHub PRs and the
reviews authored on them, and the authenticated user's Odoo task chatter.

Every puller is idempotent: each event carries a stable ``external_id`` and is
written through :meth:`LocalStateClient.add_event_dedup`, so re-running a puller
inserts nothing the second time (``INSERT OR IGNORE`` against the partial unique
index on ``events(external_id)``). Each puller returns a summary dict
(``{"inserted": n}``) and tolerates its backing tool being absent or
unauthenticated by returning ``{"skipped": <reason>}`` rather than raising.

``merge`` / ``review`` events are stored for audit but, being fixed-strategy
sources, never appear in *derived development sessions* — that exclusion is
intentional (see :data:`odoo_sdk.state.db._SESSION_SOURCE_PREDICATE`). ``commit``
and ``chatter`` events do participate in derived sessions.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from typing import Any, Optional

from odoo_sdk.state import EventRecord, LocalStateClient
from odoo_sdk.state.db import _derive_repo_label
from odoo_sdk.transport.errors import OdooError

# Task-id extractors applied to a commit/PR subject and its branch/ref context.
# Documented, ordered forms: ``#<id>`` (GitHub-style), ``odoo-<id>`` (branch
# convention, case-insensitive), and ``[<id>]`` (bracketed). Numeric ids only.
_TASK_ID_PATTERNS = (
    re.compile(r"#(\d+)"),
    re.compile(r"odoo-(\d+)", re.IGNORECASE),
    re.compile(r"\[(\d+)\]"),
)

# ASCII unit separator used to delimit git-log fields (never appears in text).
_GIT_FIELD_SEP = "\x1f"


def _extract_task_ids(subject: str, branch: str) -> list[str]:
    """Return the distinct task ids referenced in a subject/branch, in order.

    Scans ``"{subject} {branch}"`` for each documented form (``#<id>``,
    ``odoo-<id>``, ``[<id>]``) and returns the numeric ids as strings, de-duped
    with first-seen order preserved. Returns ``[]`` when nothing matches; such
    events are still stored for audit but are excluded from derived sessions by
    the ``json_array_length(task_ids) > 0`` filter.
    """
    text = f"{subject} {branch}"
    ids: list[str] = []
    for pattern in _TASK_ID_PATTERNS:
        for match in pattern.findall(text):
            if match not in ids:
                ids.append(match)
    return ids


def _run_capture(cmd: list[str]) -> Optional[str]:
    """Run ``cmd`` and return its stripped stdout, or None when it is unusable.

    Absence tolerance lives here: a missing binary (``FileNotFoundError``) and a
    non-zero exit (``CalledProcessError`` — not a repo, unauthenticated, no match)
    both collapse to ``None`` so callers can report a skip reason instead of
    raising.
    """
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _current_repo_label(state: LocalStateClient) -> str:
    """Return the ``owner/repo`` label for the current repo.

    Prefers the identity persisted into the DB (``repo_label``, stamped on
    self-resolved construction per #331); falls back to deriving it from the
    ``origin`` remote via the shared :func:`_derive_repo_label` helper so the
    label matches the one the rest of the SDK uses. Empty string when neither is
    available (events then group under the repo-less sentinel).
    """
    label = state.get_setting("repo_label")
    if label:
        return label
    remote = _run_capture(["git", "remote", "get-url", "origin"])
    return _derive_repo_label(remote) if remote else ""


def _parse_iso_utc(value: str) -> datetime:
    """Parse an offset-aware ISO-8601 timestamp (git ``%aI`` / GitHub) as UTC."""
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_odoo_dt(value: str) -> datetime:
    """Parse an Odoo naive datetime string, treating it as UTC (Odoo stores UTC)."""
    parsed = datetime.fromisoformat(value.strip().replace(" ", "T"))
    return parsed.replace(tzinfo=timezone.utc)


# ── git commits ─────────────────────────────────────────────────────────────


def _git_config_email() -> Optional[str]:
    """Return the configured git ``user.email``, or None when git is unusable."""
    return _run_capture(["git", "config", "user.email"]) or None


def _git_log(email: str) -> Optional[str]:
    """Return this repo's commit log authored by ``email``, one line per commit."""
    pretty = _GIT_FIELD_SEP.join(("%H", "%aI", "%s", "%D"))
    return _run_capture(["git", "log", f"--author={email}", f"--pretty={pretty}"])


def _store_commit(state: LocalStateClient, line: str, label: str) -> int:
    """Store one parsed git-log line as a ``commit`` event; return 1 if inserted.

    The trailing ``%D`` (ref decorations) field is optional: git omits the final
    separator when a commit carries no decoration, so the line has three fields
    (sha, date, subject) rather than four. Anything shorter is malformed and
    skipped.
    """
    parts = line.split(_GIT_FIELD_SEP)
    if len(parts) < 3:
        return 0
    sha, authored, subject = parts[0], parts[1], parts[2]
    decorations = parts[3] if len(parts) > 3 else ""
    event = EventRecord(
        id=None,
        source="commit",
        timestamp=_parse_iso_utc(authored),
        task_ids=_extract_task_ids(subject, decorations),
        repo=label,
        branch=decorations,
        subject=subject,
        external_id=f"git:{sha}",
    )
    return 1 if state.add_event_dedup(event) else 0


def sync_git_log(state: LocalStateClient) -> dict[str, Any]:
    """Reconcile this repo's authored commits into the ``events`` table.

    Reads ``git log`` filtered to the configured ``user.email`` and stores each
    commit as a ``commit`` event keyed ``git:<sha>``. Idempotent: a re-run adds
    nothing. Returns ``{"inserted": n}``, or ``{"skipped": reason}`` when git is
    absent, the email is unset, or the log cannot be read.
    """
    email = _git_config_email()
    if email is None:
        return {"skipped": "git unavailable or user.email unset"}
    log = _git_log(email)
    if log is None:
        return {"skipped": "git log failed"}
    label = _current_repo_label(state)
    inserted = sum(_store_commit(state, line, label) for line in log.splitlines() if line)
    return {"inserted": inserted}


# ── GitHub merged PRs and authored reviews ──────────────────────────────────


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


def _gh_merged_prs() -> Optional[list[dict]]:
    """Return this repo's merged PRs authored by the current user, or None."""
    return _gh_json(
        [
            "gh", "pr", "list", "--author", "@me", "--state", "merged",
            "--json", "number,title,mergedAt,headRefName",
        ]
    )


def _gh_repo_slug() -> Optional[str]:
    """Return the current repo's ``owner/repo`` slug for gh api paths, or None.

    Uses ``--jq`` so gh emits the bare slug string (not JSON), so this reads it
    with :func:`_run_capture` directly rather than JSON-decoding it.
    """
    return _run_capture(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]
    ) or None


def _store_pr(state: LocalStateClient, pr: dict, label: str) -> int:
    """Store one merged PR as a ``merge`` event; return 1 if a row was inserted."""
    merged_at = pr.get("mergedAt")
    if not merged_at:
        return 0
    number = pr["number"]
    title = pr.get("title", "")
    branch = pr.get("headRefName", "")
    event = EventRecord(
        id=None,
        source="merge",
        timestamp=_parse_iso_utc(merged_at),
        task_ids=_extract_task_ids(title, branch),
        repo=label,
        pr_num=number,
        branch=branch,
        subject=title,
        external_id=f"gh:pr:{number}",
    )
    return 1 if state.add_event_dedup(event) else 0


def _review_login(review: dict) -> str:
    """Return the login of a review's author, or empty string when absent."""
    return (review.get("user") or {}).get("login", "")


def _store_review(
    state: LocalStateClient, pr: dict, review: dict, label: str
) -> int:
    """Store one authored review as a ``review`` event; return 1 if inserted."""
    submitted = review.get("submitted_at")
    if not submitted:
        return 0
    event = EventRecord(
        id=None,
        source="review",
        timestamp=_parse_iso_utc(submitted),
        task_ids=_extract_task_ids(pr.get("title", ""), pr.get("headRefName", "")),
        repo=label,
        pr_num=pr["number"],
        branch=pr.get("headRefName", ""),
        subject=pr.get("title", ""),
        external_id=f"gh:review:{review['id']}",
    )
    return 1 if state.add_event_dedup(event) else 0


def _store_reviews(
    state: LocalStateClient,
    pr: dict,
    slug: Optional[str],
    login: str,
    label: str,
) -> int:
    """Store the current user's reviews on one PR; return the count inserted.

    No-op (returns 0) when the repo slug could not be resolved. Reviews are
    filtered to those authored by the authenticated ``login`` so a PR's other
    reviewers are never attributed to this user.
    """
    if slug is None:
        return 0
    reviews = _gh_json(["gh", "api", f"repos/{slug}/pulls/{pr['number']}/reviews"])
    if not reviews:
        return 0
    return sum(
        _store_review(state, pr, review, label)
        for review in reviews
        if _review_login(review) == login
    )


def sync_github(state: LocalStateClient) -> dict[str, Any]:
    """Reconcile merged PRs and the current user's reviews into ``events``.

    Stores each merged PR authored by the user as a ``merge`` event
    (``gh:pr:<n>``) and each review the user authored on those PRs as a
    ``review`` event (``gh:review:<id>``). Both are fixed-strategy sources, so —
    by design — they are stored for audit but never appear in derived development
    sessions. Idempotent. Returns ``{"inserted": n}``, or ``{"skipped": reason}``
    when gh is absent/unauthenticated or the PR list cannot be read.
    """
    login = _gh_login()
    if login is None:
        return {"skipped": "gh unavailable or not authenticated"}
    prs = _gh_merged_prs()
    if prs is None:
        return {"skipped": "gh pr list failed"}
    label = _current_repo_label(state)
    slug = _gh_repo_slug()
    inserted = 0
    for pr in prs:
        inserted += _store_pr(state, pr, label)
        inserted += _store_reviews(state, pr, slug, login, label)
    return {"inserted": inserted}


# ── Odoo task chatter ───────────────────────────────────────────────────────


def _current_partner_id(client: Any) -> int:
    """Return the ``res.partner`` id backing the authenticated Odoo user.

    :raises OdooError: When the authenticated user has no readable ``res.users``
        record, so the caller degrades to a skip rather than an ``IndexError``.
    """
    record = client.execute("res.users", "read", [client.uid], ["partner_id"])
    if not record:
        raise OdooError(f"no res.users record for uid {client.uid}")
    partner = record[0]["partner_id"]
    return partner[0] if isinstance(partner, (list, tuple)) else partner


def _search_chatter(client: Any, task_ids: list[int], partner_id: int) -> list[dict]:
    """Return the user's chatter messages on the tracked project tasks."""
    return client.execute(
        "mail.message",
        "search_read",
        [
            ("model", "=", "project.task"),
            ("res_id", "in", task_ids),
            ("author_id", "=", partner_id),
        ],
        fields=["id", "res_id", "date", "subject"],
    )


def _store_message(state: LocalStateClient, message: dict, label: str) -> int:
    """Store one chatter message as a ``chatter`` event; return 1 if inserted.

    A message with no timestamp (Odoo returns ``False`` for an empty datetime)
    cannot be sessionized, so it is skipped rather than crashing the puller.
    """
    date = message.get("date")
    if not date:
        return 0
    res_id = message["res_id"]
    event = EventRecord(
        id=None,
        source="chatter",
        timestamp=_parse_odoo_dt(date),
        task_ids=[str(res_id)],
        repo=label,
        subject=message.get("subject") or "",
        external_id=f"odoo:mail:{message['id']}",
    )
    return 1 if state.add_event_dedup(event) else 0


def sync_odoo_chatter(client: Any, state: LocalStateClient) -> dict[str, Any]:
    """Reconcile the user's Odoo task chatter into the ``events`` table.

    Scopes the ``mail.message`` search to the distinct task ids on record in
    ``task_runs`` and to messages authored by the authenticated uid's partner,
    storing each as a ``chatter`` event keyed ``odoo:mail:<id>``. Idempotent.
    Returns ``{"inserted": n}``, ``{"inserted": 0, "skipped": "no tracked
    tasks"}`` when nothing is tracked yet, or ``{"skipped": reason}`` when Odoo is
    unreachable.
    """
    task_ids = state.distinct_task_ids()
    if not task_ids:
        return {"inserted": 0, "skipped": "no tracked tasks"}
    label = _current_repo_label(state)
    try:
        partner_id = _current_partner_id(client)
        messages = _search_chatter(client, task_ids, partner_id)
    except OdooError:
        return {"skipped": "odoo unavailable"}
    inserted = sum(_store_message(state, message, label) for message in messages)
    return {"inserted": inserted}
