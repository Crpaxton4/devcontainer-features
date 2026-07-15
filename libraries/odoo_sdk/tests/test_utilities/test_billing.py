"""Tests for the configurable per-session minimum + rounding (issue #355).

A derived session bills its wall-clock span, which silently under-bills at the
small end: a single-event session (``MIN == MAX`` timestamp) spans zero hours
and a sub-minute session rounds toward nothing. The upload path now applies a
configurable **minimum** (floor, default ``0.25h``) and **rounding** (nearest
multiple, half-up, default ``0.05h``) at the one choke point that feeds both the
TUI ``u`` key and ``odoo-sdk upload``.

The suite has three layers:

* the pure billing math (:func:`_round_to_step` / :func:`_billable_hours`);
* the ``LocalConfig`` resolution of the two behavior knobs (defaults, file,
  environment, precedence, and invalid-value fallback);
* an end-to-end proof that a single-event session bills exactly ``0.25h`` and a
  ``1.87h`` session bills ``1.85h`` through **both** entry paths against a mocked
  Odoo transport, and that an environment override changes the billed hours.
"""

import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import odoo_sdk.cli.__main__ as cli
from odoo_sdk.state import EventRecord, LocalStateClient
from odoo_sdk.state.config import LocalConfig
from odoo_sdk.utilities.upload import (
    _billable_hours,
    _round_to_step,
    upload_sessions,
)

UTC = timezone.utc

# A session gap wide enough that the two events of the 1.87h session stay in one
# session (they are 6732s apart, well over the 3600s production default).
_WIDE_GAP = 36000

# 1.87h expressed in whole seconds (1.87 * 3600), the raw span of the multi-event
# fixture session; it rounds to 1.85h at the default 0.05h step.
_SECS_1_87H = 6732


class TestRoundToStep(unittest.TestCase):
    def test_rounds_to_nearest_multiple(self):
        self.assertEqual(_round_to_step(1.87, 0.05), 1.85)
        self.assertEqual(_round_to_step(1.86, 0.05), 1.85)
        self.assertEqual(_round_to_step(1.88, 0.05), 1.90)

    def test_half_rounds_up(self):
        # 0.025 is exactly half a 0.05 step -> rounds up to 0.05, not down.
        self.assertEqual(_round_to_step(0.025, 0.05), 0.05)

    def test_zero_step_disables_rounding(self):
        self.assertEqual(_round_to_step(1.234, 0.0), 1.234)

    def test_negative_step_disables_rounding(self):
        self.assertEqual(_round_to_step(1.234, -0.05), 1.234)


class TestBillableHours(unittest.TestCase):
    def test_single_event_session_bills_the_minimum(self):
        # Zero wall-clock span rounds to zero, then is floored up to the minimum.
        self.assertEqual(_billable_hours(0.0, 0.25, 0.05), 0.25)

    def test_sub_minimum_session_is_floored_up_never_dropped(self):
        self.assertEqual(_billable_hours(30 / 3600, 0.25, 0.05), 0.25)

    def test_rounds_to_nearest_step_above_the_minimum(self):
        self.assertEqual(_billable_hours(1.87, 0.25, 0.05), 1.85)

    def test_long_session_is_not_capped(self):
        self.assertEqual(_billable_hours(9.0, 0.25, 0.05), 9.0)

    def test_zero_step_bills_raw_span_but_honours_minimum(self):
        self.assertEqual(_billable_hours(1.234, 0.25, 0.0), 1.234)
        self.assertEqual(_billable_hours(0.1, 0.25, 0.0), 0.25)

    def test_alternate_policy_values(self):
        # min 0.5h, step 0.25h: 1.87h -> 1.75h, and a zero-span session -> 0.5h.
        self.assertEqual(_billable_hours(1.87, 0.5, 0.25), 1.75)
        self.assertEqual(_billable_hours(0.0, 0.5, 0.25), 0.5)


class TestBillingConfigResolution(unittest.TestCase):
    def test_defaults(self):
        config = LocalConfig()
        self.assertEqual(config.min_session_hours, 0.25)
        self.assertEqual(config.round_session_hours, 0.05)

    def test_file_value_wins_over_default(self):
        config = LocalConfig(
            behavior={"min_session_hours": "0.1", "round_session_hours": "0.2"}
        )
        self.assertEqual(config.min_session_hours, 0.1)
        self.assertEqual(config.round_session_hours, 0.2)

    def test_environment_override(self):
        with patch.dict(
            os.environ,
            {
                "ODOO_MIN_SESSION_HOURS": "0.5",
                "ODOO_ROUND_SESSION_HOURS": "0.25",
            },
        ):
            config = LocalConfig.load()
        self.assertEqual(config.min_session_hours, 0.5)
        self.assertEqual(config.round_session_hours, 0.25)

    def test_file_beats_environment(self):
        # Precedence is File > Environment > Default; a resolved file value wins.
        with patch.dict(os.environ, {"ODOO_MIN_SESSION_HOURS": "0.5"}):
            config = LocalConfig(behavior={"min_session_hours": "0.1"})
        self.assertEqual(config.min_session_hours, 0.1)

    def test_invalid_values_fall_back_to_default(self):
        config = LocalConfig(
            behavior={"min_session_hours": "abc", "round_session_hours": "-3"}
        )
        self.assertEqual(config.min_session_hours, 0.25)
        self.assertEqual(config.round_session_hours, 0.05)

    def test_zero_is_honoured(self):
        # Zero is a valid (disabled-rounding / no-floor) contract, not invalid.
        config = LocalConfig(
            behavior={"min_session_hours": 0, "round_session_hours": 0}
        )
        self.assertEqual(config.min_session_hours, 0.0)
        self.assertEqual(config.round_session_hours, 0.0)

    def test_boolean_is_rejected(self):
        # float(True) is 1.0, which would silently mean a one-hour floor.
        config = LocalConfig(behavior={"min_session_hours": True})
        self.assertEqual(config.min_session_hours, 0.25)


# ── End-to-end: both entry paths against a mocked Odoo transport ────────────────

_MOD = "odoo_sdk.cli.__main__"


def _seed_db() -> LocalStateClient:
    """Seed a temp DB with a single-event session and a 1.87h session.

    * task ``101`` — one lone event -> a zero-span single-event session.
    * task ``202`` — two events ``6732s`` (1.87h) apart -> one 1.87h session
      (the wide session gap keeps them from splitting).
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db = LocalStateClient(db_path=Path(tmp.name))
    base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)

    db.add_event(
        EventRecord(
            id=None,
            source="agent",
            timestamp=base,
            task_ids=["101"],
            repo="",
        )
    )
    for offset in (0, _SECS_1_87H):
        db.add_event(
            EventRecord(
                id=None,
                source="agent",
                timestamp=base + timedelta(seconds=offset),
                task_ids=["202"],
                repo="",
            )
        )
    return db


class _RecordingOdooClient:
    """Fake Odoo transport recording every call; anchors absent by design."""

    def __init__(self) -> None:
        self.uid = 7
        self.calls: list[tuple] = []
        self._next_id = 500

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((model, method, args, kwargs))
        if model == "hr.employee" and method == "search_read":
            return [{"id": 3}]
        if model == "project.task" and method == "read":
            return [{"id": args[0][0], "project_id": [9, "Proj"]}]
        if model == "account.analytic.line":
            if method == "search_read":
                return []  # no open anchor -> fresh-line create branch
            if method == "create":
                new_id = self._next_id
                self._next_id += 1
                return new_id
            if method == "write":
                return True
        raise AssertionError(f"unexpected call: {model}.{method}")

    def billed_hours_by_task(self) -> dict[int, float]:
        """Map each billed task id to the ``unit_amount`` its created row wrote."""
        billed: dict[int, float] = {}
        for model, method, args, _kwargs in self.calls:
            if model == "account.analytic.line" and method == "create":
                vals = args[0]
                billed[vals["task_id"]] = vals["unit_amount"]
        return billed


def _config(**overrides: Any) -> MagicMock:
    config = MagicMock()
    config.session_gap_secs = _WIDE_GAP
    config.min_session_hours = overrides.get("min_session_hours", 0.25)
    config.round_session_hours = overrides.get("round_session_hours", 0.05)
    return config


def _run_cli(argv: list[str], db: LocalStateClient, client: _RecordingOdooClient,
             config: MagicMock) -> str:
    """Drive ``main`` for ``argv`` against the seeded DB and fake transport."""
    out = StringIO()
    with patch(f"{_MOD}.TaskStateDB", return_value=db), patch(
        f"{_MOD}._assert_env"
    ), patch(f"{_MOD}.OdooClient", return_value=client), patch(
        f"{_MOD}.LocalConfig"
    ) as local_config, patch("sys.stdout", out), patch(
        "sys.argv", ["odoo-sdk", *argv]
    ):
        local_config.load.return_value = config
        cli.main()
    return out.getvalue()


def _run_tui(db: LocalStateClient, client: _RecordingOdooClient,
             config: MagicMock) -> tuple[int, int]:
    """Drive the seeded DB through the TUI ``u`` upload helper.

    ``upload_sessions`` is patched only to inject ``config`` (the TUI helper does
    not thread one), so the real shared billing path still runs — this proves the
    ``u`` key reaches the same minimum/rounding choke point.
    """
    from odoo_sdk.commands import Registry
    from odoo_sdk.commands.builtin import register_builtins
    from odoo_sdk.tui.app import _upload_sessions
    from odoo_sdk.tui.window import DateWindow

    registry = register_builtins(
        Registry(client, state_client=db, config=config)
    )
    window = DateWindow(date(2026, 6, 1), date(2026, 6, 7))
    sessions = registry["query_sessions"].execute(
        start_date=window.start_iso(),
        end_date=window.end_iso(),
        include_events=True,
    )
    with patch(
        "odoo_sdk.utilities.upload.LocalConfig.load", return_value=config
    ):
        return _upload_sessions(registry, sessions, window)


class TestBillingEndToEnd(unittest.TestCase):
    def test_cli_bills_minimum_and_rounded_hours(self):
        db, client = _seed_db(), _RecordingOdooClient()
        _run_cli(
            ["upload", "--start", "2026-06-01", "--end", "2026-06-07"],
            db, client, _config(),
        )
        billed = client.billed_hours_by_task()
        self.assertEqual(billed[101], 0.25)  # single-event -> minimum
        self.assertEqual(billed[202], 1.85)  # 1.87h -> nearest 0.05

    def test_tui_upload_bills_identically(self):
        db, client = _seed_db(), _RecordingOdooClient()
        _run_tui(db, client, _config())
        billed = client.billed_hours_by_task()
        self.assertEqual(billed[101], 0.25)
        self.assertEqual(billed[202], 1.85)

    def test_environment_override_changes_billed_hours(self):
        # A real LocalConfig.load() resolving the env vars, fed into the shared
        # upload path, changes what both entry paths bill: floor 0.5h, step 0.25h
        # -> single-event 0.5h and 1.87h -> 1.75h.
        db, client = _seed_db(), _RecordingOdooClient()
        with patch.dict(
            os.environ,
            {
                "ODOO_MIN_SESSION_HOURS": "0.5",
                "ODOO_ROUND_SESSION_HOURS": "0.25",
            },
        ):
            resolved = LocalConfig.load()
        sessions = [
            {
                "session_key": "101|1",
                "task_id": "101",
                "duration_secs": 0,
                "started_at": "2026-06-01T09:00:00+00:00",
                "ended_at": "2026-06-01T09:00:00+00:00",
            },
            {
                "session_key": "202|2",
                "task_id": "202",
                "duration_secs": _SECS_1_87H,
                "started_at": "2026-06-01T09:00:00+00:00",
                "ended_at": "2026-06-01T10:52:12+00:00",
            },
        ]
        upload_sessions(client, db, sessions, config=resolved)
        billed = client.billed_hours_by_task()
        self.assertEqual(billed[101], 0.5)
        self.assertEqual(billed[202], 1.75)

    def test_summary_rows_report_billed_hours_on_dry_run(self):
        # The dry-run preview must show the policy-adjusted hours a real run would
        # write, not the raw span, so callers can trust the preview.
        sessions = [
            {
                "session_key": "101|1",
                "task_id": "101",
                "duration_secs": 0,
                "started_at": "2026-06-01T09:00:00+00:00",
                "ended_at": "2026-06-01T09:00:00+00:00",
            },
        ]
        result = upload_sessions(
            MagicMock(), MagicMock(), sessions, dry_run=True, config=_config()
        )
        self.assertEqual(result["rows"][0]["hours"], 0.25)
        self.assertIsNone(result["rows"][0]["timesheet_id"])


if __name__ == "__main__":
    unittest.main()
