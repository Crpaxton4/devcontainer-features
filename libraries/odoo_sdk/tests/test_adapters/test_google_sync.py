"""Tests for the Google Calendar + Gmail resync sources (issue #370).

Everything is offline: the Google REST APIs are reached through an injected
transport callable, so a ``_FakeTransport`` dispatches on the request URL and no
network, no real credentials, and no ``google`` client library are involved. The
central tracker DB is a real fixture DB provisioned via ``create_schema`` (through
``tests.support.make_state_db``) so meetings are exercised through the REAL SQL
session derivation, not a stub.
"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from odoo_sdk.adapters import external_sync as ex
from odoo_sdk.adapters.external_sync import (
    GoogleAuthError,
    sync_gmail,
    sync_google_calendar,
)
from odoo_sdk.adapters.state_persistence import is_synthetic_tick, load_raw_events
from odoo_sdk.state import EventRecord, LocalConfig, LocalStateClient
from tests.support import make_state_db

UTC = timezone.utc
NOW = datetime(2026, 7, 15, 20, 0, tzinfo=UTC)
GAP_SECS = 3600  # the default 60-min inactivity gap


def _tmp_state() -> LocalStateClient:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return make_state_db(Path(tmp.name))


def _token_file(refreshable: bool = True, expired: bool = False) -> Path:
    """Write a token JSON and return its path.

    ``expired`` marks the stored access token stale so the refresh path runs;
    ``refreshable`` controls whether refresh credentials are present.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    expiry = NOW + timedelta(hours=1)
    if expired:
        expiry = NOW - timedelta(hours=1)
    creds = {"token": "stored-access", "expiry": expiry.isoformat()}
    if refreshable:
        creds.update(
            {
                "refresh_token": "r",
                "client_id": "cid",
                "client_secret": "sec",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        )
    Path(tmp.name).write_text(json.dumps(creds), encoding="utf-8")
    return Path(tmp.name)


def _config(token_path: Path, **behavior) -> LocalConfig:
    values = {"google_token_path": str(token_path), "google_sync_window_days": 30}
    values.update(behavior)
    return LocalConfig(behavior=values)


class _FakeTransport:
    """Dispatch Google REST calls to canned responses keyed by URL substring.

    ``calendar_items`` is returned from the events list; ``gmail_ids`` from the
    messages list; ``gmail_messages`` (id -> detail) from a message get. A token
    POST returns a fresh access token and records that a refresh happened.
    """

    def __init__(self, *, calendar_items=None, gmail_ids=None, gmail_messages=None):
        self.calendar_items = calendar_items or []
        self.gmail_ids = gmail_ids or []
        self.gmail_messages = gmail_messages or {}
        self.refreshed = False
        self.calls: list[str] = []

    def __call__(self, method, url, *, headers=None, data=None):
        self.calls.append(url)
        if "oauth2" in url and method == "POST":
            self.refreshed = True
            return {"access_token": "fresh-access", "expires_in": 3600}
        if "/calendars/primary/events" in url:
            return {"items": self.calendar_items}
        if "/users/me/messages/" in url:
            message_id = url.split("/users/me/messages/")[1].split("?")[0]
            return self.gmail_messages[message_id]
        if "/users/me/messages" in url:
            return {"messages": [{"id": mid} for mid in self.gmail_ids]}
        raise AssertionError(f"unexpected URL: {url}")


def _meeting(event_id, start, end, *, summary="Sync", status="confirmed",
             response="accepted", organized_self=False, other_attendees=True,
             event_type=None, all_day=False):
    """Build a Calendar event instance JSON for the fetch response."""
    attendees = [{"self": True, "responseStatus": response}]
    if other_attendees:
        attendees.append({"email": "peer@x.com", "responseStatus": "accepted"})
    event = {
        "id": event_id,
        "status": status,
        "summary": summary,
        "organizer": {"self": organized_self},
        "attendees": attendees,
    }
    if event_type:
        event["eventType"] = event_type
    if all_day:
        event["start"] = {"date": start}
        event["end"] = {"date": end}
    else:
        event["start"] = {"dateTime": start}
        event["end"] = {"dateTime": end}
    return event


def _sent_message(message_id, iso_ts, *, subject="Re: status", labels=("SENT",),
                  thread="t1"):
    """Build a Gmail message detail resource (metadata only)."""
    epoch_ms = int(datetime.fromisoformat(iso_ts).timestamp() * 1000)
    return {
        "id": message_id,
        "threadId": thread,
        "labelIds": list(labels),
        "internalDate": str(epoch_ms),
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "To", "value": "client@acme.com"},
                {"name": "From", "value": "me@x.com"},
                {"name": "Message-ID", "value": f"<{message_id}@x>"},
            ]
        },
    }


# ── acceptance: calendar derivation through the real SQL ─────────────────────


class TestCalendarDerivation(unittest.TestCase):
    def _run(self, items, config, transport=None):
        state = _tmp_state()
        transport = transport or _FakeTransport(calendar_items=items)
        result = sync_google_calendar(state, config, transport=transport, now=NOW)
        return state, result

    def _sessions(self, state):
        return state.derive_sessions_overlapping(
            datetime(2026, 7, 1, tzinfo=UTC),
            datetime(2026, 7, 31, tzinfo=UTC),
            gap_secs=GAP_SECS,
        )

    def test_hour_meeting_derives_exactly_one_hour(self):
        # Acceptance #1: accepted 10:00-11:00 -> ONE session of exactly 1h.
        token = _token_file()
        items = [_meeting("m1", "2026-07-15T10:00:00+00:00",
                          "2026-07-15T11:00:00+00:00", summary="Standup #24648")]
        state, result = self._run(items, _config(token))
        self.assertEqual(result, {"inserted": 13})  # 10:00..11:00 every 5 min
        sessions = self._sessions(state)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].duration_seconds, 3600.0)
        self.assertEqual(sessions[0].task_id, "24648")

    def test_twelve_minute_meeting_derives_exactly_twelve_minutes(self):
        # Acceptance #2: off-grid terminal tick lands on the true end.
        token = _token_file()
        items = [_meeting("m2", "2026-07-15T10:00:00+00:00",
                          "2026-07-15T10:12:00+00:00", summary="Chat #77777")]
        state, result = self._run(items, _config(token))
        self.assertEqual(result, {"inserted": 4})  # 0, 5, 10, 12
        sessions = self._sessions(state)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].duration_seconds, 12 * 60.0)

    def test_declined_unanswered_cancelled_and_solo_derive_nothing(self):
        # Acceptance #3 (part): non-participation produces no billable session.
        token = _token_file()
        base = ("2026-07-15T10:00:00+00:00", "2026-07-15T11:00:00+00:00")
        items = [
            _meeting("d", *base, response="declined", summary="X #1"),
            _meeting("n", *base, response="needsAction", summary="X #2"),
            _meeting("t", *base, response="tentative", summary="X #3"),
            _meeting("c", *base, status="cancelled", summary="X #4"),
            _meeting("solo", *base, other_attendees=False, summary="Deep work #5"),
            _meeting("ooo", *base, event_type="outOfOffice", summary="OOO #6"),
            _meeting("allday", "2026-07-15", "2026-07-16", all_day=True, summary="Off #7"),
        ]
        state, result = self._run(items, _config(token))
        self.assertEqual(result, {"inserted": 0})
        self.assertEqual(self._sessions(state), [])

    def test_organized_meeting_participates_even_without_accept(self):
        token = _token_file()
        items = [_meeting("org", "2026-07-15T10:00:00+00:00",
                          "2026-07-15T10:30:00+00:00", response="needsAction",
                          organized_self=True, summary="I called this #44444")]
        state, result = self._run(items, _config(token))
        self.assertEqual(result["inserted"], 7)  # 0,5,10,15,20,25,30
        self.assertEqual(len(self._sessions(state)), 1)

    def test_meeting_and_agent_work_same_task_merge_into_one_session(self):
        # Acceptance #7: one lane, not two.
        token = _token_file()
        items = [_meeting("m", "2026-07-15T10:00:00+00:00",
                          "2026-07-15T11:00:00+00:00", summary="Call #24648")]
        state, _ = self._run(items, _config(token))
        state.add_event(EventRecord(
            id=None, source="agent", timestamp=datetime(2026, 7, 15, 10, 30, tzinfo=UTC),
            task_ids=["24648"], repo=""))
        sessions = self._sessions(state)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].duration_seconds, 3600.0)

    def test_future_scheduled_meeting_is_not_fetched(self):
        # A meeting starting after ``now`` has not happened yet: timeMax=now must
        # exclude it so its ticks never bill before it occurs.
        token = _token_file()
        future = "2026-07-16T10:00:00+00:00"  # NOW is 2026-07-15 20:00
        items = [_meeting("f", future, "2026-07-16T11:00:00+00:00", summary="Later #1")]
        transport = _FakeTransport(calendar_items=items)
        state, result = self._run(items, _config(token), transport)
        # The fake ignores timeMax, so assert the puller passed timeMax=now.
        cal_url = next(u for u in transport.calls if "/calendars/" in u)
        self.assertIn("timeMax=2026-07-15T20", cal_url)

    def test_meeting_task_a_parallel_to_work_task_b_bills_two_sessions(self):
        # Acceptance #8: distinct tasks stay two concurrent lanes.
        token = _token_file()
        items = [_meeting("m", "2026-07-15T10:00:00+00:00",
                          "2026-07-15T11:00:00+00:00", summary="Call #10000")]
        state, _ = self._run(items, _config(token))
        state.add_event(EventRecord(
            id=None, source="agent", timestamp=datetime(2026, 7, 15, 10, 30, tzinfo=UTC),
            task_ids=["20000"], repo=""))
        sessions = self._sessions(state)
        self.assertEqual({s.task_id for s in sessions}, {"10000", "20000"})


# ── acceptance: reconcile (delete-series-and-re-expand) ──────────────────────


class TestCalendarReconcile(unittest.TestCase):
    def _ingest(self, state, items, token):
        return sync_google_calendar(
            state, _config(token), transport=_FakeTransport(calendar_items=items),
            now=NOW)

    def test_reingest_unchanged_inserts_nothing_and_keeps_rows(self):
        # Acceptance #6: overlapping-window re-resync -> no duplicates.
        state, token = _tmp_state(), _token_file()
        items = [_meeting("m", "2026-07-15T10:00:00+00:00",
                          "2026-07-15T11:00:00+00:00", summary="Sync #9")]
        first = self._ingest(state, items, token)
        ids_after_first = {e.id for e in state.get_events()}
        second = self._ingest(state, items, token)
        self.assertEqual(first, {"inserted": 13})
        self.assertEqual(second, {"inserted": 0})
        # Rows are preserved verbatim (same ids), not deleted and re-created.
        self.assertEqual({e.id for e in state.get_events()}, ids_after_first)

    def test_shorten_leaves_no_orphan_ticks(self):
        # Acceptance #4: a shortened meeting bills only its new span.
        state, token = _tmp_state(), _token_file()
        long = [_meeting("m", "2026-07-15T10:00:00+00:00",
                         "2026-07-15T11:00:00+00:00", summary="Sync #99999")]
        short = [_meeting("m", "2026-07-15T10:00:00+00:00",
                          "2026-07-15T10:30:00+00:00", summary="Sync #99999")]
        self._ingest(state, long, token)
        self._ingest(state, short, token)
        ticks = [e for e in state.get_events() if e.source == "calendar"]
        self.assertEqual(len(ticks), 7)  # 10:00..10:30
        self.assertEqual(max(e.timestamp for e in ticks),
                         datetime(2026, 7, 15, 10, 30, tzinfo=UTC))
        sessions = state.derive_sessions_overlapping(
            datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 7, 31, tzinfo=UTC),
            gap_secs=GAP_SECS)
        self.assertEqual(sessions[0].duration_seconds, 30 * 60.0)

    def test_move_leaves_no_ghost_series(self):
        state, token = _tmp_state(), _token_file()
        first = [_meeting("m", "2026-07-15T10:00:00+00:00",
                          "2026-07-15T11:00:00+00:00", summary="Sync #9")]
        moved = [_meeting("m", "2026-07-15T14:00:00+00:00",
                          "2026-07-15T15:00:00+00:00", summary="Sync #9")]
        self._ingest(state, first, token)
        self._ingest(state, moved, token)
        ticks = sorted(e.timestamp for e in state.get_events() if e.source == "calendar")
        self.assertEqual(ticks[0], datetime(2026, 7, 15, 14, 0, tzinfo=UTC))
        self.assertEqual(ticks[-1], datetime(2026, 7, 15, 15, 0, tzinfo=UTC))

    def test_cancellation_removes_the_whole_series(self):
        # Acceptance #3 (part): a cancellation removes an ingested series in full.
        state, token = _tmp_state(), _token_file()
        live = [_meeting("m", "2026-07-15T10:00:00+00:00",
                         "2026-07-15T11:00:00+00:00", summary="Sync #9")]
        cancelled = [_meeting("m", "2026-07-15T10:00:00+00:00",
                              "2026-07-15T11:00:00+00:00", status="cancelled",
                              summary="Sync #9")]
        self._ingest(state, live, token)
        self._ingest(state, cancelled, token)
        self.assertEqual(
            [e for e in state.get_events() if e.source == "calendar"], [])

    def test_hard_deleted_series_is_removed(self):
        state, token = _tmp_state(), _token_file()
        live = [_meeting("m", "2026-07-15T10:00:00+00:00",
                         "2026-07-15T11:00:00+00:00", summary="Sync #9")]
        self._ingest(state, live, token)
        self._ingest(state, [], token)  # event vanished entirely
        self.assertEqual(
            [e for e in state.get_events() if e.source == "calendar"], [])

    def test_task_ids_propagate_across_reschedule(self):
        # Contract with triage: an assignment made at series granularity survives.
        state, token = _tmp_state(), _token_file()
        untitled = [_meeting("m", "2026-07-15T10:00:00+00:00",
                             "2026-07-15T11:00:00+00:00", summary="Weekly sync")]
        self._ingest(state, untitled, token)
        # Simulate the triage worker assigning task 500 to the series' ticks.
        for event in state.get_events():
            if event.source == "calendar":
                event.task_ids = ["500"]
                state.delete_events([event.id])
                state.add_event(event)
        moved = [_meeting("m", "2026-07-15T14:00:00+00:00",
                          "2026-07-15T15:00:00+00:00", summary="Weekly sync")]
        self._ingest(state, moved, token)
        ticks = [e for e in state.get_events() if e.source == "calendar"]
        self.assertTrue(ticks)
        self.assertTrue(all(e.task_ids == ["500"] for e in ticks))


# ── acceptance: gmail (sent only) ───────────────────────────────────────────


class TestGmailSync(unittest.TestCase):
    def test_sent_message_becomes_an_event_received_never_does(self):
        # Acceptance #5: sent -> event; the list query never returns received mail.
        state, token = _tmp_state(), _token_file()
        transport = _FakeTransport(
            gmail_ids=["s1"],
            gmail_messages={"s1": _sent_message(
                "s1", "2026-07-15T09:00:00+00:00", subject="Update #33333")},
        )
        result = sync_gmail(state, _config(token), transport=transport, now=NOW)
        self.assertEqual(result, {"inserted": 1})
        events = [e for e in state.get_events() if e.source == "email"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].task_ids, ["33333"])
        self.assertEqual(events[0].external_id, "gmail:s1")
        self.assertEqual(events[0].payload["direction"], "sent")
        self.assertNotIn("body", events[0].payload)

    def test_non_sent_label_is_ignored_belt_and_suspenders(self):
        state, token = _tmp_state(), _token_file()
        transport = _FakeTransport(
            gmail_ids=["r1"],
            gmail_messages={"r1": _sent_message(
                "r1", "2026-07-15T09:00:00+00:00", labels=("INBOX",))},
        )
        result = sync_gmail(state, _config(token), transport=transport, now=NOW)
        self.assertEqual(result, {"inserted": 0})

    def test_reingest_skips_known_ids_without_refetch(self):
        # Acceptance #6 for email: overlapping re-sync inserts nothing.
        state, token = _tmp_state(), _token_file()
        msg = _sent_message("s1", "2026-07-15T09:00:00+00:00", subject="Hi #1")
        transport = _FakeTransport(gmail_ids=["s1"], gmail_messages={"s1": msg})
        sync_gmail(state, _config(token), transport=transport, now=NOW)
        transport.calls.clear()
        second = sync_gmail(state, _config(token), transport=transport, now=NOW)
        self.assertEqual(second, {"inserted": 0})
        # The detail endpoint is not hit again for the already-stored message.
        self.assertFalse(any("/users/me/messages/s1" in u for u in transport.calls))

    def test_subject_suppressed_when_ingest_subjects_disabled(self):
        state, token = _tmp_state(), _token_file()
        transport = _FakeTransport(
            gmail_ids=["s1"],
            gmail_messages={"s1": _sent_message(
                "s1", "2026-07-15T09:00:00+00:00", subject="Secret client #99999")},
        )
        config = _config(token, ingest_subjects=False)
        sync_gmail(state, config, transport=transport, now=NOW)
        event = [e for e in state.get_events() if e.source == "email"][0]
        self.assertEqual(event.subject, "")
        # Attribution from the marker still works even with the subject dropped.
        self.assertEqual(event.task_ids, ["99999"])


# ── acceptance: credentials + invariant ─────────────────────────────────────


class TestCredentialsAndInvariant(unittest.TestCase):
    def test_missing_credentials_raise_clear_error_naming_path(self):
        # Acceptance #10: missing creds -> clear error, not a silent zero-ingest.
        state = _tmp_state()
        missing = Path(tempfile.gettempdir()) / "does-not-exist-token.json"
        config = _config(missing)
        with self.assertRaises(GoogleAuthError) as ctx:
            sync_gmail(state, config, transport=_FakeTransport(), now=NOW)
        self.assertIn(str(missing), str(ctx.exception))
        self.assertIn("google_oauth_setup.py", str(ctx.exception))

    def test_expired_token_refreshes_then_calls_api(self):
        state, token = _tmp_state(), _token_file(expired=True)
        transport = _FakeTransport(
            gmail_ids=["s1"],
            gmail_messages={"s1": _sent_message("s1", "2026-07-15T09:00:00+00:00")})
        sync_gmail(state, _config(token), transport=transport, now=NOW)
        self.assertTrue(transport.refreshed)

    def test_expired_and_unrefreshable_raises(self):
        state = _tmp_state()
        token = _token_file(refreshable=False, expired=True)
        with self.assertRaises(GoogleAuthError):
            sync_gmail(state, _config(token), transport=_FakeTransport(), now=NOW)

    def test_gap_below_tick_config_is_rejected(self):
        # Acceptance #11: tick not strictly below gap and sweep floor -> rejected.
        state, token = _tmp_state(), _token_file()
        config = _config(token, calendar_tick_mins=60, session_gap_mins=30)
        with self.assertRaises(ValueError) as ctx:
            sync_google_calendar(state, config, transport=_FakeTransport(), now=NOW)
        self.assertIn("strictly below", str(ctx.exception))

    def test_tick_at_sweep_floor_is_rejected(self):
        state, token = _tmp_state(), _token_file()
        # gap large, but tick == sweep floor (30) is still rejected.
        config = _config(token, calendar_tick_mins=30, session_gap_mins=120)
        with self.assertRaises(ValueError):
            sync_google_calendar(state, config, transport=_FakeTransport(), now=NOW)


# ── sweep exclusion of synthetic ticks ──────────────────────────────────────


class TestSyntheticExclusion(unittest.TestCase):
    def test_ticks_excluded_from_sweep_population_but_not_derivation(self):
        state, token = _tmp_state(), _token_file()
        items = [_meeting("m", "2026-07-15T10:00:00+00:00",
                          "2026-07-15T11:00:00+00:00", summary="Sync #9")]
        sync_google_calendar(state, _config(token),
                             transport=_FakeTransport(calendar_items=items), now=NOW)
        state.add_event(EventRecord(
            id=None, source="commit", timestamp=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
            task_ids=["9"], repo="o/r"))
        full = load_raw_events(state)
        swept = load_raw_events(state, exclude_synthetic=True)
        self.assertEqual(len(full), 14)  # 13 ticks + 1 commit
        self.assertEqual(len(swept), 1)  # only the commit survives the sweep

    def test_is_synthetic_tick_predicate(self):
        tick = EventRecord(id=None, source="calendar", timestamp=NOW, task_ids=[],
                           repo="", payload={"synthetic": True})
        commit = EventRecord(id=None, source="commit", timestamp=NOW, task_ids=[],
                             repo="")
        self.assertTrue(is_synthetic_tick(tick))
        self.assertFalse(is_synthetic_tick(commit))


# ── token-path resolution ───────────────────────────────────────────────────


class TestTokenPathResolution(unittest.TestCase):
    def test_explicit_override_wins(self):
        config = LocalConfig(behavior={"google_token_path": "/tmp/tok.json"})
        self.assertEqual(ex._resolve_google_token_path(config), Path("/tmp/tok.json"))

    def test_derives_from_sdk_config_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            import os
            old = os.environ.get("ODOO_SDK_CONFIG")
            os.environ["ODOO_SDK_CONFIG"] = tmp
            try:
                resolved = ex._resolve_google_token_path(LocalConfig())
            finally:
                if old is None:
                    del os.environ["ODOO_SDK_CONFIG"]
                else:
                    os.environ["ODOO_SDK_CONFIG"] = old
        self.assertEqual(resolved, Path(tmp) / "google_token.json")


# ── low-level helpers and transport ─────────────────────────────────────────


class _PagingTransport(_FakeTransport):
    """Return a first page carrying a ``nextPageToken`` then a final page."""

    def __call__(self, method, url, *, headers=None, data=None):
        self.calls.append(url)
        if "pageToken" in url:
            if "/calendars/" in url:
                return {"items": self.calendar_items[1:]}
            return {"messages": [{"id": mid} for mid in self.gmail_ids[1:]]}
        if "/calendars/primary/events" in url:
            return {"items": self.calendar_items[:1], "nextPageToken": "p2"}
        if "/users/me/messages/" in url:
            message_id = url.split("/users/me/messages/")[1].split("?")[0]
            return self.gmail_messages[message_id]
        if "/users/me/messages" in url:
            return {
                "messages": [{"id": self.gmail_ids[0]}], "nextPageToken": "p2"
            }
        raise AssertionError(url)


class TestPaginationAndHelpers(unittest.TestCase):
    def test_calendar_pagination_collects_all_pages(self):
        state, token = _tmp_state(), _token_file()
        items = [
            _meeting("a", "2026-07-15T10:00:00+00:00", "2026-07-15T10:10:00+00:00",
                     summary="A #1"),
            _meeting("b", "2026-07-15T11:00:00+00:00", "2026-07-15T11:10:00+00:00",
                     summary="B #2"),
        ]
        transport = _PagingTransport(calendar_items=items)
        result = sync_google_calendar(state, _config(token), transport=transport, now=NOW)
        self.assertEqual(result["inserted"], 6)  # 3 ticks per 10-min meeting

    def test_gmail_pagination_collects_all_pages(self):
        state, token = _tmp_state(), _token_file()
        messages = {
            "s1": _sent_message("s1", "2026-07-15T09:00:00+00:00", subject="A #1"),
            "s2": _sent_message("s2", "2026-07-15T09:30:00+00:00", subject="B #2"),
        }
        transport = _PagingTransport(gmail_ids=["s1", "s2"], gmail_messages=messages)
        result = sync_gmail(state, _config(token), transport=transport, now=NOW)
        self.assertEqual(result["inserted"], 2)

    def test_gmail_message_without_internal_date_is_skipped(self):
        state, token = _tmp_state(), _token_file()
        msg = _sent_message("s1", "2026-07-15T09:00:00+00:00")
        del msg["internalDate"]
        transport = _FakeTransport(gmail_ids=["s1"], gmail_messages={"s1": msg})
        self.assertEqual(
            sync_gmail(state, _config(token), transport=transport, now=NOW),
            {"inserted": 0},
        )

    def test_unreadable_token_file_raises(self):
        state = _tmp_state()
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp.write(b"{not json")
        tmp.close()
        with self.assertRaises(GoogleAuthError):
            sync_gmail(state, _config(Path(tmp.name)), transport=_FakeTransport(), now=NOW)

    def test_urllib_transport_wraps_url_error(self):
        with self.assertRaises(ex.GoogleAPIError):
            ex._urllib_transport("GET", "http://127.0.0.1:1/never")

    def test_parse_google_dt_all_day_is_none(self):
        self.assertIsNone(ex._parse_google_dt({"date": "2026-07-15"}))
        self.assertIsNone(ex._parse_google_dt(None))


if __name__ == "__main__":
    unittest.main()
