"""Idempotent resync pullers reconciling local event state with external history.

Small, directory-agnostic pullers write into the unified ``events`` table so
that sessions — derived from events at query time — reflect work that happened
outside the live hook/agent stream: local git commits, GitHub PRs / reviews /
comments, Odoo task chatter, and (opt-in) Google Calendar meetings and sent
Gmail. Three issues drive this surface: **#378** widened resync capture (commits
across all branches, opened as well as merged PRs, reviews on others' PRs, and
author-wide chatter, with a batched ``project.task`` existence check that keeps
well-formed-but-nonexistent ids from minting phantom session lanes), **#370**
added the Google sources, and **#652**/**#653** made capture location-independent:
the git puller recursively discovers every checkout under the current directory
(any depth, including the cwd itself), the GitHub puller searches the account
(``gh search prs --author``) instead of the current repo, and review events
carry their PR's real ``headRefName`` so branch-encoded task ids attribute.

Every puller accepts optional inclusive ``start``/``end`` dates that override
the rolling ``resync_window_days`` window (mirroring the upload command's range
semantics), and is idempotent: each event carries a stable ``external_id``
written through :meth:`LocalStateClient.add_event_dedup` (``INSERT OR IGNORE``
against the partial unique index on ``events(external_id)``), so re-running
inserts nothing the second time. The error-vs-skip contract (#652): a source
whose tooling is entirely unusable (no git identity, zero repos, unauthenticated
gh, a failed account-wide search) returns ``{"error": reason}`` so callers can
fail loudly, while a partial degradation (one unreadable repo, one missing PR
detail) is tolerated and reported in the summary counters. ``{"skipped": ...}``
remains for the genuinely optional paths (an unconfigured Odoo). The Google
pullers instead raise — see below.

``merge`` events are stored for audit only: a merge is a point-in-time release
marker, not a work span, so it is the one ingested source excluded from derived
sessions (see :data:`odoo_sdk.state.db._SESSION_SOURCE_PREDICATE`). Opening a
PR, by contrast, IS billable work — assembling and describing the change is at
least a review's worth of effort — so each authored PR also mints a
``pr_opened`` event at ``createdAt`` (#656). Every non-merge source
participates — ``commit``/``chatter`` as the development family and
``review``/``comment``/``pr_opened`` as the gap-windowed review family (#396,
#656); a window of purely review-family events is labeled "Review".
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from odoo_sdk.adapters.state_persistence import _SYNTHETIC_PAYLOAD_KEY
from odoo_sdk.sessionization.config import SessionizationConfig
from odoo_sdk.state import EventRecord, LocalConfig, LocalStateClient
from odoo_sdk.state.config import _DEFAULT_RESYNC_WINDOW_DAYS
from odoo_sdk.state.db import _derive_repo_label, _normalize_utc_isoformat
from odoo_sdk.transport.errors import OdooError

# Minimum task-id magnitude. Real Odoo task ids are 4-5 digits; requiring at
# least this many digits kills false positives where a short client-side number
# (``#31 - Hardcode…``) or a PR cross-reference (``(#189)``) minted a phantom
# task lane (issue #378 item 1).
_MIN_TASK_ID_DIGITS = 4

# Task-id extractors applied to a commit/PR subject and its branch/ref context.
# Documented, ordered forms (all require >= ``_MIN_TASK_ID_DIGITS`` digits):
#   ``#<id>``          GitHub-style reference
#   ``odoo-<id>``      branch convention (case-insensitive)
#   ``[<id>]``         bracketed
#   ``<id>-slug``      branch-prefix convention used on client branches (#622:
#                      id BEFORE the ``-``); anchored to a token start so a
#                      digit run buried mid-token never reads as an id
#   ``task <id>``      PR-title form ``(task NNNNN)`` (optional space/hyphen)
#   ``(<id>)``         trailing ``(NNNNN)`` in a PR title (NOT ``(#NNNNN)``)
#   ``<id> title``     bare leading id at subject START (space/#/:/- after the
#                      digits, issue #654), GATED behind ``allow_leading_id``
#                      (git/GitHub call sites only). ``^`` is safe: the scanned
#                      text is ``f"{subject} {branch}"``, so a branch never
#                      sits at string start, and the join space delimits a
#                      bare-only title; a leading ``NNNNN-`` subject also
#                      matches the ``<id>-slug`` form — dedupe absorbs it
_TASK_ID_PATTERNS = (
    re.compile(rf"#(\d{{{_MIN_TASK_ID_DIGITS},}})"),
    re.compile(rf"odoo-(\d{{{_MIN_TASK_ID_DIGITS},}})", re.IGNORECASE),
    re.compile(rf"\[(\d{{{_MIN_TASK_ID_DIGITS},}})\]"),
    re.compile(rf"(?:^|[\s,/])(\d{{{_MIN_TASK_ID_DIGITS},}})-"),
    re.compile(rf"\btask[ -]?(\d{{{_MIN_TASK_ID_DIGITS},}})\b", re.IGNORECASE),
    re.compile(rf"\((\d{{{_MIN_TASK_ID_DIGITS},}})\)"),
)

# The gated ``<id> title`` form above. Kept OUT of ``_TASK_ID_PATTERNS`` so the
# calendar/gmail call sites (explicit-marker contract) can never grow it by
# accident; :func:`_extract_task_ids` appends it LAST when ``allow_leading_id``
# is set, preserving first-seen dedupe precedence.
_LEADING_TASK_ID_PATTERN = re.compile(rf"^(\d{{{_MIN_TASK_ID_DIGITS},}})[\s#:-]")

# ASCII unit separator used to delimit git-log fields (never appears in text).
_GIT_FIELD_SEP = "\x1f"

# Payload key flagging extracted task ids that FAILED the ``project.task``
# existence check at resync time (issue #378 item 1). Such ids are kept OUT of
# the event's ``task_ids`` (no phantom lane) but recorded here so the TUI triage
# surface can present the event as a WEAK candidate; a non-empty list means weak.
_UNVALIDATED_TASK_IDS_KEY = "unvalidated_task_ids"


def _extract_task_ids(
    subject: str, branch: str, *, allow_leading_id: bool = False
) -> list[str]:
    """Return the distinct task ids in ``"{subject} {branch}"``, first-seen order.

    Every form requires at least :data:`_MIN_TASK_ID_DIGITS` digits, so a short
    client-side number or a PR cross-reference is never mistaken for a task id.
    ``allow_leading_id`` additionally admits the bare-leading-id form
    (:data:`_LEADING_TASK_ID_PATTERN`, issue #654); only the git/GitHub call
    sites pass True — calendar/gmail attribution stays explicit-marker only.
    """
    text = f"{subject} {branch}"
    patterns = _TASK_ID_PATTERNS
    if allow_leading_id:
        patterns += (_LEADING_TASK_ID_PATTERN,)
    ids: list[str] = []
    for pattern in patterns:
        for match in pattern.findall(text):
            if match not in ids:
                ids.append(match)
    return ids


def _validate_task_ids(client: Any, ids: set[str]) -> Optional[set[str]]:
    """Return the subset of ``ids`` that name a real ``project.task`` (issue #378).

    One batched ``search_read`` over the whole id set. Returns ``None`` when
    validation cannot run — no client, or Odoo unreachable — so the caller trusts
    the extracted ids as-is (best-effort offline); returns ``set()`` when the
    client is present but ``ids`` is empty. Every id is a digit run by
    construction (all come from :func:`_extract_task_ids`).
    """
    if client is None:
        return None
    assert all(i.isdigit() for i in ids)
    numeric = sorted({int(i) for i in ids})
    if not numeric:
        return set()
    try:
        rows = client.execute(
            "project.task", "search_read", [("id", "in", numeric)], fields=["id"]
        )
    except OdooError:
        return None
    return {str(row["id"]) for row in rows}


def _finalize_task_attribution(event: EventRecord, valid: Optional[set[str]]) -> None:
    """Move an event's unvalidated ids out of ``task_ids`` into the weak flag.

    Mutates ``event`` in place: validated ids stay in ``task_ids`` (they bill
    normally); unvalidated ids are dropped (so the event never joins a session)
    and recorded under :data:`_UNVALIDATED_TASK_IDS_KEY`. ``valid=None`` means
    validation did not run, so every id is trusted as-is.
    """
    if valid is None:
        return
    known = [i for i in event.task_ids if i in valid]
    unknown = [i for i in event.task_ids if i not in valid]
    event.task_ids = known
    if unknown:
        payload = dict(event.payload) if event.payload else {}
        payload[_UNVALIDATED_TASK_IDS_KEY] = unknown
        event.payload = payload


def _store_pending(
    state: LocalStateClient, pending: list[EventRecord], client: Any
) -> list[EventRecord]:
    """Validate every pending event's ids in ONE batch, then store them deduped.

    Returns the NEWLY inserted events (dedup skips are omitted) so callers can
    both count insertions and report on first-sight rows only — e.g. the
    unattributed-review warning must not re-fire for an already-stored (and
    possibly since-triaged) review on every subsequent resync.
    """
    all_ids = {i for event in pending for i in event.task_ids}
    valid = _validate_task_ids(client, all_ids)
    inserted: list[EventRecord] = []
    for event in pending:
        _finalize_task_attribution(event, valid)
        if state.add_event_dedup(event):
            inserted.append(event)
    return inserted


def _run_capture(cmd: list[str]) -> Optional[str]:
    """Run ``cmd`` and return its stripped stdout, or None when it is unusable.

    A missing binary (``FileNotFoundError``) and a non-zero exit
    (``CalledProcessError`` — not a repo, unauthenticated, no match) both collapse
    to ``None`` so callers can report a skip reason instead of raising.
    """
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _current_repo_label(state: LocalStateClient) -> str:
    """Return the current repo's ``owner/repo`` label, or empty string.

    Prefers the identity persisted into the DB (``repo_label``, stamped per #331);
    falls back to deriving it from the ``origin`` remote. Used only by the Odoo
    chatter puller since #652 — the git puller labels each discovered repo from
    its OWN origin via :func:`_repo_label_for`, never this cwd-scoped value.
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


def _window_start(config: Optional[LocalConfig], now: Optional[datetime]) -> datetime:
    """Return the inclusive lower bound ``now - resync_window_days`` in UTC (#378).

    ``now`` defaults to the current UTC time and is injectable for deterministic
    tests.
    """
    days = config.resync_window_days if config else _DEFAULT_RESYNC_WINDOW_DAYS
    moment = now or datetime.now(timezone.utc)
    return moment - timedelta(days=days)


def _window_bounds(
    config: Optional[LocalConfig],
    now: Optional[datetime],
    start: Optional[date],
    end: Optional[date],
) -> tuple[datetime, datetime]:
    """Resolve one resync run's half-open ``[since, until)`` capture window.

    An explicit inclusive ``start``/``end`` date overrides the rolling default,
    matching :func:`odoo_sdk.billing.upload.range_bounds`'s inclusive-date math:
    ``since`` is UTC midnight of ``start`` and ``until`` is UTC midnight of the
    day AFTER ``end`` (so the whole end day is covered). Without them, ``since``
    falls back to :func:`_window_start` and ``until`` to ``now``.

    :raises ValueError: When the resolved window is empty or inverted (e.g. an
        ``end`` older than the rolling window's start, or ``start`` after
        ``end``) — the range must fail loudly rather than silently sweeping
        nothing.
    """
    since = (
        datetime.combine(start, time.min, tzinfo=timezone.utc)
        if start
        else _window_start(config, now)
    )
    until = (
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
        if end
        else (now or datetime.now(timezone.utc))
    )
    if since >= until:
        raise ValueError(
            f"empty resync window: since {since.isoformat()} is not before "
            f"until {until.isoformat()} (an end-only range must lie inside the "
            "rolling window; pass --start to widen it)"
        )
    return since, until


def _git_author_emails(config: Optional[LocalConfig]) -> list[str]:
    """Return the git author emails to filter commits by (issue #378 item 4).

    The configured identities that look like emails; when none are configured,
    falls back to the single ``git user.email``. Multiple emails are OR-ed by
    ``git log`` via repeated ``--author`` flags. Resolved ONCE per resync, not
    per discovered repo: global git config resolves outside any checkout, so a
    per-repo lookup would only repeat the same answer.
    """
    configured = [a for a in (config.resync_authors if config else []) if "@" in a]
    if configured:
        return configured
    email = _run_capture(["git", "config", "user.email"])
    return [email] if email else []


# Directories the repo discovery never descends into: the VCS dir itself and
# obviously-vendored/generated trees. Deliberately minimal (user decision on
# #652): a nested checkout anywhere ELSE — at any depth, including below
# another repo root — is a real repo and is discovered.
_PRUNE_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__"})


def _discover_git_repos(root: Path) -> list[Path]:
    """Return every git checkout at or under ``root`` (any depth), sorted (#652).

    A directory is a repo root when it contains a ``.git`` entry — a directory
    for a plain clone, or a *gitfile* for worktrees/submodules (``.exists()``
    covers both). Descent prunes only :data:`_PRUNE_DIRS`; discovery keeps
    walking below repo roots, so nested checkouts are found too (the branch
    overlap this can introduce dedupes on the ``git:<sha>`` unique index).
    ``followlinks=False`` guards against symlink loops. When ``root`` itself is
    a lone checkout the result is exactly ``[root]`` — the prior single-repo
    behavior.
    """
    repos: list[Path] = []
    for dirpath, dirnames, _filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE_DIRS]
        if (Path(dirpath) / ".git").exists():
            repos.append(Path(dirpath))
    return sorted(repos)


def _dedupe_shared_stores(repos: list[Path]) -> list[Path]:
    """Collapse checkouts sharing one object store onto a single root (#652).

    Linked worktrees (gitfile ``.git``) share the main clone's store, so reading
    each would return the same commits once per worktree — inflating ``found``
    and wasting subprocesses (``git:<sha>`` dedup keeps the DB correct either
    way). Keyed on ``git rev-parse --git-common-dir``: linked worktrees resolve
    to the main clone's ``.git`` and collapse onto the first (sorted) root,
    while submodules keep their own ``modules/<name>`` dir and survive. A repo
    whose rev-parse fails falls back to its own path (kept).
    """
    seen: set[str] = set()
    kept: list[Path] = []
    for root in repos:
        common = _run_capture(
            [
                "git", "-C", str(root), "rev-parse",
                "--path-format=absolute", "--git-common-dir",
            ]
        )
        key = common or str(root)
        if key not in seen:
            seen.add(key)
            kept.append(root)
    return kept


def _git_log(
    root: Path, emails: list[str], since: datetime, until: datetime
) -> Optional[str]:
    """Return commits by ``emails`` across ALL branches of the repo at ``root``.

    ``-C root`` scopes the read to one discovered checkout regardless of the
    process cwd (#652). ``--all`` (issue #378 item 2) makes unmerged branch work
    visible — the work most likely to be unlogged — while ``git:<sha>`` external
    ids dedupe the branch overlap ``--all`` introduces. The full-ISO
    ``--since``/``--until`` pair applies the resolved window exactly.
    """
    pretty = _GIT_FIELD_SEP.join(("%H", "%aI", "%s", "%D"))
    cmd = [
        "git", "-C", str(root), "log", "--all", f"--pretty={pretty}",
        f"--since={since.isoformat()}", f"--until={until.isoformat()}",
    ]
    cmd.extend(f"--author={email}" for email in emails)
    return _run_capture(cmd)


def _repo_label_for(root: Path) -> str:
    """Derive one discovered repo's ``owner/repo`` label from its own origin.

    Deliberately does NOT consult the DB ``repo_label`` setting: that is a
    single global value, and stamping it onto every discovered repo would label
    them all identically. Each repo's label comes from its OWN ``origin`` remote
    (empty when it has none).
    """
    remote = _run_capture(["git", "-C", str(root), "remote", "get-url", "origin"])
    return _derive_repo_label(remote) if remote else ""


def _build_commit_event(line: str, label: str) -> Optional[EventRecord]:
    """Build one ``commit`` event from a git-log line, or None when malformed.

    The trailing ``%D`` (ref decorations) field is optional — git omits the final
    separator for an undecorated commit, so a valid line has three fields (sha,
    date, subject) or four. Task ids are validated later by :func:`_store_pending`.
    """
    parts = line.split(_GIT_FIELD_SEP)
    if len(parts) < 3:
        return None
    sha, authored, subject = parts[0], parts[1], parts[2]
    decorations = parts[3] if len(parts) > 3 else ""
    return EventRecord(
        id=None,
        source="commit",
        timestamp=_parse_iso_utc(authored),
        task_ids=_extract_task_ids(subject, decorations, allow_leading_id=True),
        repo=label,
        branch=decorations,
        subject=subject,
        external_id=f"git:{sha}",
    )


def _repo_commit_events(
    root: Path, emails: list[str], since: datetime, until: datetime
) -> Optional[list[EventRecord]]:
    """Read one discovered repo's commits as events, or None when its log fails."""
    log = _git_log(root, emails, since, until)
    if log is None:
        return None
    label = _repo_label_for(root)
    return [
        event
        for line in log.splitlines()
        if line
        if (event := _build_commit_event(line, label)) is not None
    ]


def sync_git_log(
    state: LocalStateClient,
    config: Optional[LocalConfig] = None,
    client: Any = None,
    *,
    now: Optional[datetime] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> dict[str, Any]:
    """Reconcile authored commits from every repo under the cwd (#378, #652).

    Recursively discovers git checkouts at/below the current directory and reads
    each one's ``git log --all`` over the resolved window, filtered to the
    configured author emails and labeled from each repo's OWN origin remote.
    Commits store as ``commit`` events keyed ``git:<sha>`` (task ids validated
    in one batch when ``client`` is supplied). Idempotent. Entirely-unusable git
    — no author identity, zero repos, or every repo's log failing — is a hard
    ``{"error": reason}``; a single failing repo only counts in
    ``failed_repos``. ``found`` vs ``inserted`` distinguishes "nothing to
    capture" from "captured nothing new" (#652 item 4).
    """
    try:
        since, until = _window_bounds(config, now, start, end)
    except ValueError as exc:
        return {"error": str(exc)}
    emails = _git_author_emails(config)
    if not emails:
        return {"error": "git unavailable or user.email unset"}
    repos = _dedupe_shared_stores(_discover_git_repos(Path.cwd()))
    if not repos:
        return {"error": f"no git repositories under {Path.cwd()}"}
    pending: list[EventRecord] = []
    failed = 0
    for root in repos:
        events = _repo_commit_events(root, emails, since, until)
        if events is None:
            failed += 1
        else:
            pending.extend(events)
    if failed == len(repos):
        return {"error": f"git log failed in all {len(repos)} repositories"}
    result: dict[str, Any] = {
        "inserted": len(_store_pending(state, pending, client)),
        "found": len(pending),
        "repos": len(repos),
    }
    if failed:
        result["failed_repos"] = failed
    return result


# ── GitHub merged PRs and authored reviews ──────────────────────────────────


@dataclass(frozen=True)
class _Window:
    """The resolved half-open ``[since, until)`` window for one GitHub resync.

    Slim on purpose (#652): the old per-resync context also carried the current
    repo's label/slug, but an account-wide capture derives every event's repo
    from its own PR's ``repository.nameWithOwner`` instead.
    """

    since: datetime
    until: datetime


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
            "gh", "search", "prs", "--author", login,
            "--updated", f">={window.since.date().isoformat()}",
            "--limit", str(_AUTHORED_SEARCH_LIMIT),
            "--json", "number,title,repository,state,createdAt,updatedAt",
        ]
    )


def _gh_pr_detail(slug: str, number: int) -> dict:
    """Fetch one PR's ``headRefName``/``mergedAt`` — fields the search JSON lacks.

    Degrades to ``{}`` on failure so the event is still minted (branch-less),
    mirroring :func:`_collect_review_family`'s per-parent tolerance: one
    unreadable PR never drops the rest of the capture.
    """
    return _gh_json(
        ["gh", "pr", "view", str(number), "-R", slug, "--json", "headRefName,mergedAt"]
    ) or {}


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
    """
    ts = _ts_in_window(review.get("submitted_at"), window.since, window.until)
    if ts is None:
        return None
    branch = pr.get("headRefName", "")
    title = pr.get("title", "")
    return EventRecord(
        id=None,
        source="review",
        timestamp=ts,
        task_ids=_extract_task_ids(title, branch, allow_leading_id=True),
        repo=repo,
        pr_num=pr.get("number", 0),
        branch=branch,
        subject=title,
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
        subs = _gh_json(
            ["gh", "api", "--paginate", api_path(api_slug, parent["number"])]
        ) or []
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
    return _gh_json(
        [
            "gh", "search", "prs", "--reviewed-by", login,
            "--updated", f">={window.since.date().isoformat()}",
            "--limit", str(_FAMILY_SEARCH_LIMIT),
            "--json", "number,title,repository",
        ]
    ) or []


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
    return _gh_json(
        [
            "gh", "search", "issues", "--commenter", login,
            "--updated", f">={window.since.date().isoformat()}",
            "--limit", str(_FAMILY_SEARCH_LIMIT),
            "--json", "number,title,repository",
        ]
    ) or []


def _comment_events(
    window: _Window, login: str, commented: list[dict]
) -> list[EventRecord]:
    """Return ``login``'s authored issue/PR comments across repos (issue #378 #3)."""
    parents = [
        (item, repo, repo)
        for item in commented
        if (repo := _repo_of(item))
    ]
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
    state: LocalStateClient,
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


def _odoo_dt_str(moment: datetime) -> str:
    """Format a UTC datetime as Odoo's naive ``YYYY-MM-DD HH:MM:SS`` string."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _search_chatter(
    client: Any, partner_id: int, since: datetime, until: datetime
) -> list[dict]:
    """Return the user's authored task chatter over ``[since, until]`` (issue #378 #5).

    Author-wide over ALL ``project.task`` messages, NOT just tracked tasks — the
    biggest manual finding was unlogged work on tasks never started locally.
    """
    return client.execute(
        "mail.message",
        "search_read",
        [
            ("model", "=", "project.task"),
            ("author_id", "=", partner_id),
            ("date", ">=", _odoo_dt_str(since)),
            ("date", "<=", _odoo_dt_str(until)),
        ],
        fields=["id", "res_id", "date", "subject"],
    )


def _store_message(state: LocalStateClient, message: dict, label: str) -> int:
    """Store one chatter message as a ``chatter`` event; return 1 if inserted.

    A message with no timestamp (Odoo returns ``False`` for an empty datetime) is
    skipped rather than crashing the puller. The task id is the message's
    ``res_id`` (the task it is ON), so it exists by construction.
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


def sync_odoo_chatter(
    client: Any,
    state: LocalStateClient,
    config: Optional[LocalConfig] = None,
    *,
    now: Optional[datetime] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> dict[str, Any]:
    """Reconcile the user's Odoo task chatter into the ``events`` table (issue #378).

    Searches ``mail.message`` author-wide over the resolved window (item 5;
    explicit ``start``/``end`` override the rolling default, #652) and stores
    each as a ``chatter`` event keyed ``odoo:mail:<id>``. Idempotent.
    Returns ``{"inserted": n}``, ``{"skipped": reason}``, or ``{"error": reason}``
    for an empty/inverted window.
    """
    try:
        since, until = _window_bounds(config, now, start, end)
    except ValueError as exc:
        return {"error": str(exc)}
    label = _current_repo_label(state)
    try:
        partner_id = _current_partner_id(client)
        messages = _search_chatter(client, partner_id, since, until)
    except OdooError:
        return {"skipped": "odoo unavailable"}
    inserted = sum(_store_message(state, message, label) for message in messages)
    return {"inserted": inserted}


# ── Google Calendar + Gmail (issue #370) ────────────────────────────────────
#
# Two opt-in resync sources (never in the default source string) that reach the
# Google REST APIs directly over stdlib ``urllib`` behind an injected transport
# callable, so the SDK carries no third-party Google dependency and tests run
# fully offline. Credentials are host-provisioned: a token JSON written by
# ``scripts/google_oauth_setup.py`` into the existing ``~/.config/odoo_sdk`` mount
# is CONSUMED here (refreshed via a plain token-endpoint POST when stale). The SDK
# never runs the OAuth flow and never mints credentials. Ingesting zero events
# silently is the forbidden failure mode (acceptance #10), so these pullers RAISE
# on unusable credentials rather than returning a skip.
#
# **Email — active participation only.** Only messages the user SENT are ingested
# (Gmail ``in:sent``); received mail is never a row. Each sent message is one
# point event keyed ``gmail:<id>``, metadata only (message-id, thread-id,
# participants, direction, timestamp) — never the body.
#
# **Calendar — participation only, expanded to a tick train.** A meeting the user
# organized or accepted is expanded into synthetic point events ``calendar_tick_mins``
# apart with a terminal tick on the exact end time, so the UNCHANGED gap
# derivation reconstructs it as one session. Declined/tentative/unanswered,
# cancelled, all-day, OOO/focus/busy furniture, and solo blocks are excluded.
# Reconcile is delete-the-series-and-re-expand keyed on the parent event id, so a
# reschedule/extend/shorten/cancel never leaves an orphan tick; task_ids are
# propagated from the prior series' ticks so a triage assignment survives re-sync.

_CAL_API_BASE = "https://www.googleapis.com/calendar/v3"
_GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"
_GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"

# Calendar ``eventType`` values that are furniture, not meetings.
_EXCLUDED_EVENT_TYPES = frozenset({"outOfOffice", "focusTime", "workingLocation"})
# The only responseStatus that counts as participation (besides organizing).
_ACCEPTED_STATUS = "accepted"

_CALENDAR_SOURCE = "calendar"
_EMAIL_SOURCE = "email"
_TICK_MARKER = ":tick:"
_DEFAULT_TOKEN_FILENAME = "google_token.json"

# The sweep floor the tick interval must stay strictly below (acceptance #11); a
# tick at or above it would let a meeting shatter into per-tick minimum-billed
# sessions since ``optimize_sessions`` never scans below this gap.
_SWEEP_MIN_GAP_MINS = SessionizationConfig().sweep_min_gap_mins

# Injected HTTP transport: ``transport(method, url, *, headers=None, data=None)``
# returns the parsed JSON body. ``data`` (a form dict) marks a POST body. Tests
# pass a fake that dispatches on the URL; production uses :func:`_urllib_transport`.
GoogleTransport = Callable[..., dict]


class GoogleAuthError(RuntimeError):
    """Raised when Google credentials are missing, unreadable, or unrefreshable.

    Carries a single actionable message naming the token path and the fix; the
    calendar/gmail pullers raise this rather than degrading to a skip so
    credentials failures are never silent (acceptance #10).
    """


class GoogleAPIError(RuntimeError):
    """Raised when a Google REST call fails at the transport layer."""


def _urllib_transport(
    method: str,
    url: str,
    *,
    headers: Optional[dict] = None,
    data: Optional[dict] = None,
) -> dict:
    """Perform one HTTP call over stdlib ``urllib`` and JSON-decode the body.

    ``data`` (a mapping) is form-encoded and marks a POST body. Any
    transport-level failure is surfaced as :class:`GoogleAPIError`.
    """
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    request = urllib.request.Request(
        url, data=body, method=method, headers=headers or {}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise GoogleAPIError(f"{method} {url} failed: {exc}") from exc


# ── credentials ─────────────────────────────────────────────────────────────


def _resolve_google_token_path(config: LocalConfig) -> Path:
    """Return the path the Google token JSON is read from (issue #370).

    Precedence: an explicit ``google_token_path`` override, then a path derived
    from the ``ODOO_SDK_CONFIG`` mount, then ``~/.config/odoo_sdk``.
    """
    explicit = config.google_token_path
    if explicit:
        return Path(explicit).expanduser()
    sdk_config = os.environ.get("ODOO_SDK_CONFIG")
    if sdk_config:
        base = Path(sdk_config).expanduser()
        directory = base if base.is_dir() else base.parent
        return directory / _DEFAULT_TOKEN_FILENAME
    return Path("~/.config/odoo_sdk").expanduser() / _DEFAULT_TOKEN_FILENAME


def _google_creds_error(path: Path, reason: str) -> GoogleAuthError:
    """Build the single actionable credentials error naming the path and fix."""
    return GoogleAuthError(
        f"Google credentials unusable ({reason}): {path}. Re-run the host helper "
        "`python3 scripts/google_oauth_setup.py` to (re)authorize Calendar and "
        "Gmail read-only access and write a fresh token file there."
    )


def _load_google_credentials(path: Path) -> dict:
    """Read and parse the token JSON, raising a clear error when unusable."""
    if not path.exists():
        raise _google_creds_error(path, "no token file")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _google_creds_error(path, "token file is not readable JSON") from exc


def _token_is_current(creds: dict, now: datetime) -> bool:
    """Whether the stored access token is present and not past its expiry.

    A token with no recorded ``expiry`` is trusted as-is (nothing proves it stale).
    """
    if not creds.get("token"):
        return False
    expiry = creds.get("expiry")
    if not expiry:
        return True
    return _parse_iso_utc(expiry) > now


def _refresh_access_token(
    creds: dict, path: Path, transport: GoogleTransport
) -> str:
    """Exchange the refresh token for a fresh access token via a token POST."""
    refresh_token = creds.get("refresh_token")
    client_id = creds.get("client_id")
    client_secret = creds.get("client_secret")
    if not (refresh_token and client_id and client_secret):
        raise _google_creds_error(path, "expired and no refresh credentials")
    token_uri = creds.get("token_uri") or _GOOGLE_TOKEN_URI
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    try:
        response = transport("POST", token_uri, data=payload)
    except GoogleAPIError as exc:
        raise _google_creds_error(path, "token refresh failed") from exc
    access = response.get("access_token")
    if not access:
        raise _google_creds_error(path, "token refresh returned no access_token")
    return access


def _google_access_token(
    config: LocalConfig, transport: GoogleTransport, now: datetime
) -> str:
    """Resolve a usable Google access token, refreshing the stored one if stale."""
    path = _resolve_google_token_path(config)
    creds = _load_google_credentials(path)
    if _token_is_current(creds, now):
        return creds["token"]
    return _refresh_access_token(creds, path, transport)


def _google_get(url: str, token: str, transport: GoogleTransport) -> dict:
    """Perform one authenticated GET and return the parsed JSON body."""
    return transport("GET", url, headers={"Authorization": f"Bearer {token}"})


def _google_pages(
    url_builder: Callable[[Optional[str]], str],
    token: str,
    transport: GoogleTransport,
) -> Iterator[dict]:
    """Yield each page's JSON body, following ``nextPageToken`` to exhaustion.

    ``url_builder`` takes the current page token (None on the first request) and
    returns the URL to fetch.
    """
    page_token: Optional[str] = None
    while True:
        data = _google_get(url_builder(page_token), token, transport)
        yield data
        page_token = data.get("nextPageToken")
        if not page_token:
            break


# ── calendar ────────────────────────────────────────────────────────────────


def _validate_tick_interval(config: LocalConfig) -> None:
    """Reject a tick interval not strictly below the gap and sweep floor (#11).

    At or above the gap (or sweep floor) a meeting's ticks would stop chaining
    into one session and each would independently bill the per-session minimum, so
    the invariant is asserted loudly at resync.
    """
    tick = config.calendar_tick_mins
    gap = config.session_gap_mins
    if tick >= gap or tick >= _SWEEP_MIN_GAP_MINS:
        raise ValueError(
            f"calendar_tick_mins ({tick}) must be strictly below both the session "
            f"gap ({gap} min) and the sweep floor ({_SWEEP_MIN_GAP_MINS} min); "
            "otherwise each meeting shatters into per-tick minimum-billed sessions."
        )


def _parse_google_dt(node: Optional[dict]) -> Optional[datetime]:
    """Parse a Calendar ``start``/``end`` node to UTC, or None for an all-day one.

    A timed event carries ``dateTime`` (offset-aware ISO); an all-day event
    carries only ``date`` and yields None.
    """
    if not node:
        return None
    date_time = node.get("dateTime")
    if not date_time:
        return None
    return _parse_iso_utc(date_time)


def _self_attendee(event: dict) -> Optional[dict]:
    """Return the attendee entry flagged ``self``, or None."""
    for attendee in event.get("attendees", []):
        if attendee.get("self"):
            return attendee
    return None


def _has_other_attendees(event: dict) -> bool:
    """Whether the event has at least one human attendee other than the user.

    Solo blocks (no other attendees) are furniture, not meetings. Resource rows
    (rooms) do not count as people.
    """
    for attendee in event.get("attendees", []):
        if attendee.get("self") or attendee.get("resource"):
            continue
        return True
    return False


def _self_participated(event: dict) -> bool:
    """Whether the user organized the event or accepted the invite.

    Organizing counts regardless of ``responseStatus``; otherwise only an explicit
    ``accepted`` counts (declined/tentative/needsAction do not).
    """
    if (event.get("organizer") or {}).get("self"):
        return True
    attendee = _self_attendee(event)
    return bool(attendee) and attendee.get("responseStatus") == _ACCEPTED_STATUS


def _meeting_span(event: dict) -> Optional[tuple[datetime, datetime]]:
    """Return a participated meeting's ``(start, end)`` span, or None to exclude it.

    Applies the participation filter (cancelled, furniture, all-day, solo,
    declined) and, when the event qualifies, returns its parsed span so callers
    need not re-parse ``start``/``end``.
    """
    if event.get("status") == "cancelled":
        return None
    if event.get("eventType") in _EXCLUDED_EVENT_TYPES:
        return None
    start = _parse_google_dt(event.get("start"))
    end = _parse_google_dt(event.get("end"))
    if start is None or end is None:  # all-day or malformed
        return None
    if not _has_other_attendees(event):
        return None
    if not _self_participated(event):
        return None
    return (start, end)


def _expand_ticks(start: datetime, end: datetime, tick_mins: int) -> list[datetime]:
    """Return point-event timestamps spanning ``[start, end]`` with a terminal end.

    Ticks land every ``tick_mins`` minutes from the start; a final tick is always
    placed on the EXACT end so the derived session's ``MAX-MIN`` span is the true
    meeting duration even when the end is off the tick grid (a 12-min meeting →
    0, 5, 10, 12). A meeting shorter than one tick emits just its start and end.
    The strict ``<`` guard means a grid-aligned end is never duplicated.
    """
    step = timedelta(minutes=tick_mins)
    ticks: list[datetime] = []
    moment = start
    while moment < end:
        ticks.append(moment)
        moment += step
    ticks.append(end)
    return ticks


def _series_id_of(external_id: Optional[str]) -> Optional[str]:
    """Return the series key encoded in a tick's ``external_id``, or None."""
    if not external_id or _TICK_MARKER not in external_id:
        return None
    return external_id.split(_TICK_MARKER, 1)[0]


def _tick_external_id(series_id: str, moment: datetime) -> str:
    """Return the stable, synthetic-marked external id for one tick.

    Keying on the tick's ISO timestamp (not an index) makes a moved or resized
    meeting produce a different id set, so the reconcile diff detects the change.
    """
    return f"{series_id}{_TICK_MARKER}{_normalize_utc_isoformat(moment)}"


def _desired_ticks(
    event: dict, series_id: str, tick_mins: int
) -> list[tuple[str, datetime]]:
    """Return the (external_id, timestamp) ticks a fetched event should produce.

    Empty when the event fails the participation filter, which drives the
    reconcile to remove any existing series for it.
    """
    span = _meeting_span(event)
    if span is None:
        return []
    start, end = span
    return [(_tick_external_id(series_id, m), m) for m in _expand_ticks(start, end, tick_mins)]


def _propagate_task_ids(
    event: Optional[dict], existing_rows: list[EventRecord]
) -> list[str]:
    """Resolve the task ids for a (re-)expanded series.

    An explicit ``#id`` / ``[id]`` marker in the meeting title always attributes
    (and refreshes on every resync). Otherwise the prior series' ticks' task ids
    are propagated forward so a triage assignment survives a reschedule; a series
    with neither stays inert (``[]``).
    """
    if event is not None:
        subject_ids = _extract_task_ids(event.get("summary", ""), "")
        if subject_ids:
            return subject_ids
    propagated: list[str] = []
    for row in existing_rows:
        for task_id in row.task_ids:
            if task_id not in propagated:
                propagated.append(task_id)
    return propagated


def _make_tick_event(
    external_id: str,
    moment: datetime,
    series_id: str,
    task_ids: list[str],
    subject: str,
) -> EventRecord:
    """Build one synthetic calendar tick :class:`EventRecord`."""
    return EventRecord(
        id=None,
        source=_CALENDAR_SOURCE,
        timestamp=moment,
        task_ids=list(task_ids),
        repo="",
        subject=subject,
        external_id=external_id,
        payload={
            _SYNTHETIC_PAYLOAD_KEY: True,
            "series": series_id,
            "kind": "calendar_tick",
        },
    )


def _insert_tick_series(
    state: LocalStateClient,
    desired: list[tuple[str, datetime]],
    series_id: str,
    task_ids: list[str],
    subject: str,
) -> int:
    """Insert every desired tick, returning how many new rows were written."""
    inserted = 0
    for external_id, moment in desired:
        tick = _make_tick_event(external_id, moment, series_id, task_ids, subject)
        if state.add_event_dedup(tick):
            inserted += 1
    return inserted


def _reconcile_series(
    state: LocalStateClient,
    series_id: str,
    event: Optional[dict],
    existing_rows: list[EventRecord],
    config: LocalConfig,
) -> int:
    """Reconcile one meeting's tick series to its desired shape; return inserts.

    Delete-series-and-re-expand keyed on the parent event id: when the desired
    tick set already matches what is stored, nothing changes (preserving any
    triage assignment). On ANY difference the whole existing series is deleted and
    the desired ticks inserted fresh, so no orphan or duplicate can survive.
    """
    desired = _desired_ticks(event, series_id, config.calendar_tick_mins) if event else []
    if {row.external_id for row in existing_rows} == {ext for ext, _ in desired}:
        return 0
    stale_ids = [row.id for row in existing_rows if row.id is not None]
    if stale_ids:
        state.delete_events(stale_ids)
    task_ids = _propagate_task_ids(event, existing_rows)
    subject = (event or {}).get("summary", "") if config.ingest_subjects else ""
    return _insert_tick_series(state, desired, series_id, task_ids, subject)


def _calendar_events_url(
    time_min: datetime, time_max: datetime, page_token: Optional[str]
) -> str:
    """Build the Calendar ``events.list`` URL for the reconcile window."""
    params = {
        "timeMin": _normalize_utc_isoformat(time_min),
        "timeMax": _normalize_utc_isoformat(time_max),
        "singleEvents": "true",
        "showDeleted": "true",
        "maxResults": "250",
        "orderBy": "startTime",
    }
    if page_token:
        params["pageToken"] = page_token
    return f"{_CAL_API_BASE}/calendars/primary/events?{urllib.parse.urlencode(params)}"


def _fetch_calendar_items(
    token: str,
    transport: GoogleTransport,
    time_min: datetime,
    time_max: datetime,
) -> list[dict]:
    """Page through every calendar event instance in the reconcile window."""
    items: list[dict] = []
    for page in _google_pages(
        lambda tok: _calendar_events_url(time_min, time_max, tok), token, transport
    ):
        items.extend(page.get("items", []))
    return items


def _load_existing_calendar_series(
    state: LocalStateClient, time_min: datetime, time_max: datetime
) -> dict[str, list[EventRecord]]:
    """Group stored calendar ticks in the window by their parent series id."""
    series: dict[str, list[EventRecord]] = {}
    for record in state.get_events(time_min, time_max):
        if record.source != _CALENDAR_SOURCE:
            continue
        series_id = _series_id_of(record.external_id)
        if series_id is not None:
            series.setdefault(series_id, []).append(record)
    return series


def sync_google_calendar(
    state: LocalStateClient,
    config: LocalConfig,
    *,
    transport: GoogleTransport = _urllib_transport,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Reconcile accepted/organized meetings into synthetic tick series (#370).

    Validates the tick invariant (acceptance #11), resolves a host-provisioned
    Google token, fetches every event instance in the window, and reconciles each
    parent event's tick series delete-and-re-expand. Series no longer returned
    (hard-deleted) are removed too. Idempotent. Returns ``{"inserted": n}``.

    :raises ValueError: When the tick interval violates the gap/sweep invariant.
    :raises GoogleAuthError: When credentials are missing, expired, or unrefreshable.
    """
    now = now or datetime.now(timezone.utc)
    _validate_tick_interval(config)
    token = _google_access_token(config, transport, now)
    radius = timedelta(days=config.google_sync_window_days)
    # Fetch only meetings that have STARTED (``timeMax=now``): a purely-future
    # scheduled meeting is not billable work yet, so ingesting its tick train
    # would let an upload window bill an hour before the meeting happens. An
    # in-progress meeting (start < now, end > now) is still fetched and expanded
    # to its full scheduled span. The EXISTING-tick load still spans forward so an
    # in-progress meeting's already-written future ticks are seen whole and the
    # reconcile stays a clean no-op.
    time_min = now - radius
    items = _fetch_calendar_items(token, transport, time_min, now)
    existing = _load_existing_calendar_series(state, time_min, now + radius)
    # ``gcal:<id>`` series key pairs with :func:`_series_id_of` on the tick ids.
    items_by_series = {f"gcal:{item['id']}": item for item in items}
    inserted = 0
    for series_id in set(existing) | set(items_by_series):
        inserted += _reconcile_series(
            state,
            series_id,
            items_by_series.get(series_id),
            existing.get(series_id, []),
            config,
        )
    return {"inserted": inserted}


# ── gmail ───────────────────────────────────────────────────────────────────


def _gmail_list_url(query: str, page_token: Optional[str]) -> str:
    """Build the Gmail ``messages.list`` URL for a sent-only query."""
    params = {"q": query, "maxResults": "500"}
    if page_token:
        params["pageToken"] = page_token
    return f"{_GMAIL_API_BASE}/users/me/messages?{urllib.parse.urlencode(params)}"


def _fetch_sent_message_ids(
    token: str, transport: GoogleTransport, after: datetime
) -> list[str]:
    """Return the ids of messages the user SENT since ``after`` (sent-only).

    The ``in:sent after:<epoch>`` query guarantees received mail, CCs, and list
    traffic never appear — receiving is not participation (acceptance #5).
    """
    query = f"in:sent after:{int(after.timestamp())}"
    return [
        message["id"]
        for page in _google_pages(
            lambda tok: _gmail_list_url(query, tok), token, transport
        )
        for message in page.get("messages", [])
    ]


def _existing_gmail_ids(
    state: LocalStateClient, start: datetime, end: datetime
) -> set[str]:
    """Return the external ids of sent-mail events already stored in the window."""
    return {
        record.external_id
        for record in state.get_events(start, end)
        if record.source == _EMAIL_SOURCE and record.external_id
    }


def _gmail_get_url(message_id: str) -> str:
    """Build the metadata-only Gmail ``messages.get`` URL (no body is fetched)."""
    headers = ("From", "To", "Cc", "Subject", "Message-ID", "Date")
    query = "&".join(f"metadataHeaders={name}" for name in headers)
    return f"{_GMAIL_API_BASE}/users/me/messages/{message_id}?format=metadata&{query}"


def _gmail_headers(message: dict) -> dict[str, str]:
    """Return the message's headers as a lower-cased name→value mapping."""
    payload = message.get("payload") or {}
    return {
        header.get("name", "").lower(): header.get("value", "")
        for header in payload.get("headers", [])
    }


def _gmail_timestamp(message: dict) -> Optional[datetime]:
    """Return the message's send time from ``internalDate`` (ms epoch), or None."""
    internal = message.get("internalDate")
    if not internal:
        return None
    try:
        return datetime.fromtimestamp(int(internal) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def _store_sent_message(
    state: LocalStateClient,
    message: dict,
    config: LocalConfig,
) -> int:
    """Store one sent Gmail message as an ``email`` point event; 1 if inserted.

    Metadata only — never the body. Attribution is by an explicit ``#id`` /
    ``[id]`` marker in the subject; without one the event is inert (``task_ids=[]``).
    """
    timestamp = _gmail_timestamp(message)
    if timestamp is None:
        return 0
    headers = _gmail_headers(message)
    subject = headers.get("subject", "")
    event = EventRecord(
        id=None,
        source=_EMAIL_SOURCE,
        timestamp=timestamp,
        task_ids=_extract_task_ids(subject, ""),
        repo="",
        subject=subject if config.ingest_subjects else "",
        external_id=f"gmail:{message['id']}",
        payload={
            "thread_id": message.get("threadId", ""),
            "message_id": headers.get("message-id", ""),
            "to": headers.get("to", ""),
            "cc": headers.get("cc", ""),
            "from": headers.get("from", ""),
            "direction": "sent",
        },
    )
    return 1 if state.add_event_dedup(event) else 0


def sync_gmail(
    state: LocalStateClient,
    config: LocalConfig,
    *,
    transport: GoogleTransport = _urllib_transport,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Reconcile the user's SENT Gmail into ``email`` point events (issue #370).

    Resolves a host-provisioned Google token and ingests each message sent within
    the window as a metadata-only point event keyed ``gmail:<id>``. Received mail
    is never touched (acceptance #5). Idempotent: already-stored messages are
    skipped without re-fetching detail. Returns ``{"inserted": n}``.

    :raises GoogleAuthError: When credentials are missing, expired, or unrefreshable.
    """
    now = now or datetime.now(timezone.utc)
    token = _google_access_token(config, transport, now)
    after = now - timedelta(days=config.google_sync_window_days)
    already = _existing_gmail_ids(state, after, now)
    inserted = 0
    for message_id in _fetch_sent_message_ids(token, transport, after):
        if f"gmail:{message_id}" in already:
            continue
        detail = _google_get(_gmail_get_url(message_id), token, transport)
        inserted += _store_sent_message(state, detail, config)
    return {"inserted": inserted}
