"""The ``upload`` builtin derives sessions for the range and bills them."""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from odoo_dev_data.events import EventRecord

from ...fakes import FakeRpcClient, service
from ...support import make_event_store

_UPLOAD = "odoo_dev_core.builtins.upload.upload_sessions"


class TestUploadBuiltin(unittest.TestCase):
    def _seed(self):
        db = make_event_store()
        db.add_event(
            EventRecord(
                id=None,
                source="commit",
                timestamp=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),
                task_ids=["101"],
                repo="o/r",
            )
        )
        return db

    def test_derives_sessions_for_the_range_then_bills_them(self):
        db = self._seed()
        client = FakeRpcClient()
        svc = service(client=client, events=db)
        with patch(_UPLOAD, return_value={"uploaded": 1}) as upload:
            result = svc.execute(
                "upload", start_date="2026-06-01", end_date="2026-06-01", dry_run=True
            )
        self.assertEqual(result, {"uploaded": 1})
        args, kwargs = upload.call_args
        self.assertIs(args[0], client)
        self.assertIs(args[1], db)
        self.assertEqual([s["task_id"] for s in args[2]], ["101"])
        self.assertNotIn("events", args[2][0])
        self.assertEqual(kwargs["start_date"], "2026-06-01")
        self.assertEqual(kwargs["end_date"], "2026-06-01")
        self.assertTrue(kwargs["dry_run"])
        self.assertIs(kwargs["config"], svc.services.config)

    def test_given_sessions_are_billed_without_deriving(self):
        svc = service()
        given = [{"session_key": "1|1", "task_id": "1"}]
        with patch(_UPLOAD, return_value={}) as upload:
            svc.execute("upload", sessions=given)
        self.assertIs(upload.call_args.args[2], given)


if __name__ == "__main__":
    unittest.main()
