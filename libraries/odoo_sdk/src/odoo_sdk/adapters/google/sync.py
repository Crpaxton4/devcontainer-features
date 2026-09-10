"""Google Calendar + Gmail resync adapters (issue #370; relocated by #718).

The Google adapter package's implementation module: opt-in pullers that
reconcile accepted/organized meetings (as synthetic tick series) and SENT
Gmail (as metadata-only point events) into the unified ``events`` table.
Relocated verbatim from :mod:`odoo_sdk.adapters.external_sync` under the
one-package-per-external-system layout (ADR-005 amendment, #718); the old
module re-exports every public and test-read name so historical imports keep
working. Core reaches these pullers through the
:class:`~odoo_sdk.commands.protocols.CalendarGateway` port, which this module
satisfies structurally (PEP 544 module-implements-protocol).

See the section commentary below (kept intact from the original module) for
the participation rules, the tick-train expansion, and the credential
contract.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from odoo_sdk.adapters._shared import _extract_task_ids, _parse_iso_utc
from odoo_sdk.adapters.state.persistence import _SYNTHETIC_PAYLOAD_KEY
from odoo_sdk.sessionization.config import SessionizationConfig
from odoo_sdk.state import EventRecord, LocalConfig, LocalStateClient
from odoo_sdk.state.db import _normalize_utc_isoformat

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


def _refresh_access_token(creds: dict, path: Path, transport: GoogleTransport) -> str:
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
    return [
        (_tick_external_id(series_id, m), m)
        for m in _expand_ticks(start, end, tick_mins)
    ]


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
    desired = (
        _desired_ticks(event, series_id, config.calendar_tick_mins) if event else []
    )
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
