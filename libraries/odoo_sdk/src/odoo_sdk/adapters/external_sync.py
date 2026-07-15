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
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from odoo_sdk.adapters.state_persistence import _SYNTHETIC_PAYLOAD_KEY
from odoo_sdk.sessionization.config import SessionizationConfig
from odoo_sdk.state import EventRecord, LocalConfig, LocalStateClient
from odoo_sdk.state.db import _derive_repo_label, _normalize_utc_isoformat
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


# ── Google Calendar + Gmail (issue #370) ────────────────────────────────────
#
# Two opt-in resync sources (never in the default source string) that reach the
# Google REST APIs directly over stdlib ``urllib`` behind an injected transport
# callable, so the SDK carries no third-party Google dependency and tests run
# fully offline. Credentials are host-provisioned: a token JSON written by
# ``scripts/google_oauth_setup.py`` into the existing ``~/.config/odoo_sdk`` mount
# is CONSUMED here (refreshed via a plain token-endpoint POST when stale). The SDK
# never runs the OAuth flow and never mints credentials.
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
# The synthetic-tick payload marker is OWNED by state_persistence (the reader that
# excludes ticks from the sweep); imported here so the writer and reader can never
# drift to different literals.

# The sweep floor the tick interval must stay strictly below (acceptance #11); the
# ``optimize_sessions`` sweep will not scan below this gap, so a tick at or above
# it would let a meeting shatter into per-tick minimum-billed sessions.
_SWEEP_MIN_GAP_MINS = SessionizationConfig().sweep_min_gap_mins

# Injected HTTP transport: ``transport(method, url, *, headers=None, data=None)``
# returns the parsed JSON body. ``data`` (a form dict) marks a POST body. Tests
# pass a fake that dispatches on the URL; production uses :func:`_urllib_transport`.
GoogleTransport = Callable[..., dict]


class GoogleAuthError(RuntimeError):
    """Raised when Google credentials are missing, unreadable, or unrefreshable.

    Carries a single actionable message naming the token path and the fix
    (re-run the host OAuth helper). Ingesting zero events silently is the
    forbidden failure mode (acceptance #10), so the calendar/gmail pullers raise
    this rather than degrading to a skip when credentials cannot be used.
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

    ``data`` (a mapping) is form-encoded and marks a POST; its presence never
    changes ``method`` (the caller passes both explicitly). Any transport-level
    failure — connection error or non-2xx status — is surfaced as
    :class:`GoogleAPIError` so callers can translate it into the appropriate
    auth/skip decision rather than leaking a ``urllib`` exception.
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

    Precedence: an explicit ``google_token_path`` config/env override, then a
    path derived from the existing ``ODOO_SDK_CONFIG`` mount (its directory, or
    the parent of a config *file*), then ``~/.config/odoo_sdk``. The token is
    host-provisioned and bind-mounted alongside the other SDK config; the SDK
    only reads it.
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

    A token with no recorded ``expiry`` is trusted as-is (nothing proves it
    stale); a token whose ``expiry`` is at or before ``now`` is treated as
    expired so the refresh path runs.
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


# ── calendar ────────────────────────────────────────────────────────────────


def _validate_tick_interval(config: LocalConfig) -> None:
    """Reject a tick interval not strictly below the gap and sweep floor (#11).

    If the tick interval were at or above the inactivity gap (or the sweep
    floor), a meeting's ticks would no longer chain into one session — every
    tick would split off and independently bill the per-session minimum, turning
    a 1-hour meeting into N minimum-billed sessions. The invariant is asserted at
    resync so the misconfiguration is rejected loudly rather than silently
    shattering meetings.
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
    carries only ``date`` and is not a meeting, so it yields None (the caller
    excludes it upstream, but returning None keeps this total).
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

    Solo blocks (no other attendees) are calendar furniture, not meetings, so
    they are excluded regardless of how the user responded. Resource rows (rooms)
    do not count as people.
    """
    for attendee in event.get("attendees", []):
        if attendee.get("self") or attendee.get("resource"):
            continue
        return True
    return False


def _self_participated(event: dict) -> bool:
    """Whether the user organized the event or accepted the invite.

    Organizing counts regardless of ``responseStatus``; otherwise only an
    explicit ``accepted`` counts — declined, tentative, and needsAction (never
    answered) are not participation.
    """
    if (event.get("organizer") or {}).get("self"):
        return True
    attendee = _self_attendee(event)
    return bool(attendee) and attendee.get("responseStatus") == _ACCEPTED_STATUS


def _should_ingest_calendar_event(event: dict) -> bool:
    """Apply the participation filter to one Calendar event instance."""
    if event.get("status") == "cancelled":
        return False
    if event.get("eventType") in _EXCLUDED_EVENT_TYPES:
        return False
    start = _parse_google_dt(event.get("start"))
    end = _parse_google_dt(event.get("end"))
    if start is None or end is None:  # all-day or malformed
        return False
    if not _has_other_attendees(event):
        return False
    return _self_participated(event)


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


def _series_id_for(event_id: str) -> str:
    """Return the parent-event series key an event's ticks are grouped under."""
    return f"gcal:{event_id}"


def _series_id_of(external_id: Optional[str]) -> Optional[str]:
    """Return the series key encoded in a tick's ``external_id``, or None."""
    if not external_id or _TICK_MARKER not in external_id:
        return None
    return external_id.split(_TICK_MARKER, 1)[0]


def _tick_external_id(series_id: str, moment: datetime) -> str:
    """Return the stable, synthetic-marked external id for one tick.

    Keying on the tick's ISO timestamp (not an index) makes a moved or resized
    meeting produce a different id set, so the reconcile diff naturally detects
    the change; the ``:tick:`` marker keeps it recognizably synthetic.
    """
    return f"{series_id}{_TICK_MARKER}{_normalize_utc_isoformat(moment)}"


def _desired_ticks(
    event: dict, series_id: str, tick_mins: int
) -> list[tuple[str, datetime]]:
    """Return the (external_id, timestamp) ticks a fetched event should produce.

    Empty when the event fails the participation filter (declined, cancelled,
    solo, furniture, all-day), which drives the reconcile to remove any existing
    series for it.
    """
    if not _should_ingest_calendar_event(event):
        return []
    start = _parse_google_dt(event["start"])
    end = _parse_google_dt(event["end"])
    assert start is not None and end is not None  # guaranteed by the filter
    return [(_tick_external_id(series_id, m), m) for m in _expand_ticks(start, end, tick_mins)]


def _propagate_task_ids(
    event: Optional[dict], existing_rows: list[EventRecord]
) -> list[str]:
    """Resolve the task ids for a (re-)expanded series.

    An explicit ``#id`` / ``[id]`` marker in the meeting title always attributes
    (and refreshes on every resync). Otherwise the prior series' ticks' task ids
    are propagated forward so a triage assignment made at series granularity
    survives a reschedule; a series with neither stays inert (``[]``).
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


def _tick_subject(event: Optional[dict], config: LocalConfig) -> str:
    """Return the meeting title to store on ticks, honoring ``ingest_subjects``."""
    if not config.ingest_subjects:
        return ""
    return (event or {}).get("summary", "")


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
    tick set (by external id) already matches what is stored, nothing changes and
    the stored rows — including any triage task assignment — are preserved. On
    ANY difference (reschedule, extend, shorten, cancel, or first ingest) the
    whole existing series is deleted and the desired ticks are inserted fresh, so
    no orphan tick can survive and no duplicate can be created.
    """
    desired = _desired_ticks(event, series_id, config.calendar_tick_mins) if event else []
    if {row.external_id for row in existing_rows} == {ext for ext, _ in desired}:
        return 0
    stale_ids = [row.id for row in existing_rows if row.id is not None]
    if stale_ids:
        state.delete_events(stale_ids)
    task_ids = _propagate_task_ids(event, existing_rows)
    return _insert_tick_series(
        state, desired, series_id, task_ids, _tick_subject(event, config)
    )


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
    page_token: Optional[str] = None
    while True:
        url = _calendar_events_url(time_min, time_max, page_token)
        data = _google_get(url, token, transport)
        items.extend(data.get("items", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break
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
    Google token (raising a clear error when unusable, acceptance #10), fetches
    every event instance in the ``google_sync_window_days`` window each side of
    now, and reconciles each parent event's tick series delete-and-re-expand.
    Series present in the store but no longer returned (hard-deleted) are removed
    too. Idempotent: an unchanged window inserts nothing. Returns
    ``{"inserted": n}``.

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
    # to its full scheduled span — the accepted scheduled-span default. The
    # EXISTING-tick load still spans forward so an in-progress meeting's already
    # -written future ticks are seen whole and the reconcile stays a clean no-op.
    time_min = now - radius
    items = _fetch_calendar_items(token, transport, time_min, now)
    existing = _load_existing_calendar_series(state, time_min, now + radius)
    items_by_series = {_series_id_for(item["id"]): item for item in items}
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
    ids: list[str] = []
    page_token: Optional[str] = None
    while True:
        data = _google_get(_gmail_list_url(query, page_token), token, transport)
        ids.extend(message["id"] for message in data.get("messages", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return ids


def _existing_gmail_ids(
    state: LocalStateClient, start: datetime, end: datetime
) -> set[str]:
    """Return the external ids of sent-mail events already stored in the window.

    Lets the puller skip re-fetching message detail for messages it has already
    ingested, so an overlapping-window re-sync is cheap and inserts nothing.
    """
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


def _is_sent_message(message: dict) -> bool:
    """Belt-and-suspenders check that a fetched message really is sent mail."""
    labels = message.get("labelIds")
    return labels is None or "SENT" in labels


def _store_sent_message(
    state: LocalStateClient,
    message: dict,
    config: LocalConfig,
) -> int:
    """Store one sent Gmail message as an ``email`` point event; 1 if inserted.

    Metadata only — message-id, thread-id, participants, direction, timestamp —
    never the body. Attribution is by an explicit ``#id`` / ``[id]`` marker in
    the subject; without one the event is inert (``task_ids=[]``) by design.
    """
    if not _is_sent_message(message):
        return 0
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

    Resolves a host-provisioned Google token (raising a clear error when
    unusable, acceptance #10) and ingests each message sent within the
    ``google_sync_window_days`` backward window as a metadata-only point event
    keyed ``gmail:<id>``. Received mail is never touched (acceptance #5).
    Idempotent: already-stored messages are skipped without re-fetching detail,
    so an overlapping-window re-sync inserts nothing. Returns ``{"inserted": n}``.

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
