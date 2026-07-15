"""End-to-end tests for the headless ``odoo-sdk prune`` subcommand (issue #363).

Seeds a temporary state DB with aged hook events split across an uploaded session
and an un-uploaded session, then drives ``main`` for real. ``prune`` is a
local-only subcommand — it constructs no ``OdooClient`` and writes nothing to
Odoo — so the acceptance recipe runs against the live SQLite DB: a ``--dry-run``
previews the billable-safe set without touching anything, and a real prune deletes
the uploaded session's aged events, keeps the un-uploaded session, leaves the
retained window's derivation byte-identical, and keeps the upload ledger
consistent.
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import odoo_sdk.cli.__main__ as cli
from odoo_sdk.state import EventRecord
from tests.support import make_state_db

_MOD = "odoo_sdk.cli.__main__"
UTC = timezone.utc
GAP = 3600  # one hour, matching the default session gap
# A fixed "now" the seeded ages are relative to; the CLI uses the real clock, so
# the aged events sit 40 days back and recent events 2 days back — both stable
# against a 30-day horizon regardless of when the suite runs.
_AGED_DAYS = 40
_RECENT_DAYS = 2


def _seed_db():
    """Return (db, ids) seeded with an uploaded aged session, an un-uploaded aged
    session, a recent session, and an untargeted aged diagnostic event."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db = make_state_db(Path(tmp.name))
    now = datetime.now(UTC)
    aged = now - timedelta(days=_AGED_DAYS)
    recent = now - timedelta(days=_RECENT_DAYS)

    def add(ts, task_ids):
        return db.add_event(
            EventRecord(id=None, source="agent", timestamp=ts,
                        task_ids=list(task_ids), repo="")
        )

    ids = {}
    ids["up1"] = add(aged, ("101",)).id
    ids["up2"] = add(aged + timedelta(seconds=GAP), ("101",)).id
    ids["un1"] = add(aged + timedelta(hours=5), ("202",)).id
    ids["un2"] = add(aged + timedelta(hours=5, seconds=GAP), ("202",)).id
    ids["diag"] = add(aged + timedelta(hours=1), ()).id
    ids["rec1"] = add(recent, ("303",)).id
    ids["rec2"] = add(recent + timedelta(seconds=GAP), ("303",)).id

    # Mark the task-101 session uploaded.
    up_first = db.get_event(ids["up1"])
    up_last = db.get_event(ids["up2"])
    db.record_session_upload(
        f"101|{ids['up1']}", 500, 1.0, task_id="101",
        started_at=up_first.timestamp, ended_at=up_last.timestamp,
    )
    return db, ids


def _run_cli(argv, db):
    """Drive ``main`` for ``argv`` against the seeded DB (local-only, no Odoo)."""
    out = StringIO()
    with patch(f"{_MOD}.TaskStateDB", return_value=db), patch(
        "sys.stdout", out
    ), patch("sys.argv", ["odoo-sdk", *argv]):
        cli.main()
    return out.getvalue()


class TestCmdPrune(unittest.TestCase):
    def test_prune_is_local_only(self):
        # A prune writes nothing to Odoo, so it must skip the devcontainer assert.
        self.assertIn("prune", cli._LOCAL_ONLY)

    def test_dry_run_previews_without_deleting(self):
        db, ids = _seed_db()
        out = _run_cli(["prune", "--older-than", "30", "--dry-run"], db)

        self.assertIn("Would prune 3 event(s)", out)  # up1, up2, diag
        self.assertIn("would retire 1 upload mapping(s)", out)
        # Nothing actually deleted.
        for key in ids:
            self.assertIsNotNone(db.get_event(ids[key]))
        self.assertIsNotNone(db.get_session_upload(f"101|{ids['up1']}"))

    def test_real_prune_deletes_uploaded_aged_keeps_the_rest(self):
        db, ids = _seed_db()

        lo = datetime.now(UTC) - timedelta(days=5)
        hi = datetime.now(UTC) + timedelta(days=1)
        before = db.derive_sessions_overlapping(lo, hi, gap_secs=GAP)
        snapshot = [(w.task_id, w.id, w.event_ids) for w in before]

        out = _run_cli(["prune", "--older-than", "30"], db)
        self.assertIn("Pruned 3 event(s)", out)
        self.assertIn("retired 1 upload mapping(s)", out)

        # Uploaded aged session + diagnostic gone.
        self.assertIsNone(db.get_event(ids["up1"]))
        self.assertIsNone(db.get_event(ids["up2"]))
        self.assertIsNone(db.get_event(ids["diag"]))
        # Un-uploaded aged session survives (the guard).
        self.assertIsNotNone(db.get_event(ids["un1"]))
        self.assertIsNotNone(db.get_event(ids["un2"]))
        # Recent session survives.
        self.assertIsNotNone(db.get_event(ids["rec1"]))
        self.assertIsNotNone(db.get_event(ids["rec2"]))
        # Ledger consistent: the deleted session's mapping was retired.
        self.assertIsNone(db.get_session_upload(f"101|{ids['up1']}"))
        # Retained window derivation byte-identical.
        after = db.derive_sessions_overlapping(lo, hi, gap_secs=GAP)
        self.assertEqual(snapshot, [(w.task_id, w.id, w.event_ids) for w in after])

    def test_no_horizon_configured_is_a_no_op(self):
        db, ids = _seed_db()
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("ODOO_PRUNE_HORIZON_DAYS", None)
            out = _run_cli(["prune"], db)
        self.assertIn("Auto-prune disabled", out)
        self.assertIsNotNone(db.get_event(ids["up1"]))

    def test_configured_horizon_honored_when_flag_omitted(self):
        db, ids = _seed_db()
        with patch.dict(
            "os.environ", {"ODOO_PRUNE_HORIZON_DAYS": "30"}, clear=False
        ):
            out = _run_cli(["prune"], db)
        self.assertIn("Pruned 3 event(s)", out)
        self.assertIsNone(db.get_event(ids["up1"]))

    def test_invalid_older_than_rejected_cleanly(self):
        db, _ = _seed_db()
        with self.assertRaises(SystemExit) as ctx:
            _run_cli(["prune", "--older-than", "notanint"], db)
        self.assertEqual(ctx.exception.code, 2)

    def test_non_positive_older_than_rejected(self):
        # 0/negative would set the cutoff to now/future and flush every closed
        # session; the flag must be a positive day count (exit 2, clean message).
        db, ids = _seed_db()
        for bad in ("0", "-5"):
            with self.assertRaises(SystemExit) as ctx:
                _run_cli(["prune", "--older-than", bad], db)
            self.assertEqual(ctx.exception.code, 2)
        self.assertIsNotNone(db.get_event(ids["up1"]))  # nothing deleted


if __name__ == "__main__":
    unittest.main()
