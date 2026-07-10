"""The single guard test for the system-wide ``unlink`` ban.

Record deletion via ``unlink`` is purposefully not implemented for safety
(irrecoverable data loss). A correct implementation never calls it — this guard
is permanent idiot-proofing that must fail hard and loudly if it is ever
reached. This is the ONLY test in the suite that references ``unlink``; every
other path to a delete has been removed, so nothing else should ever trip it.

It asserts the shared ``forbid_unlink`` guard raises ``DeletionNotSupportedError``
at BOTH public execute seams (``OdooClient.execute`` and
``OdooRecordset._execute``), before any executor delegation — so even an injected
test executor cannot let an ``unlink`` through.
"""

import unittest
from unittest.mock import Mock

from odoo_sdk.client import OdooClient
from odoo_sdk.records.recordset import OdooRecordset
from odoo_sdk.transport.errors import DeletionNotSupportedError, forbid_unlink
from odoo_sdk.transport.executor import OdooExecutor


class TestUnlinkForbidden(unittest.TestCase):
    def test_forbid_unlink_raises_on_unlink(self):
        with self.assertRaises(DeletionNotSupportedError):
            forbid_unlink("unlink")

    def test_forbid_unlink_is_noop_for_other_methods(self):
        # Non-delete methods must pass through untouched.
        for method in ("read", "write", "create", "search", "search_read"):
            forbid_unlink(method)  # must not raise

    def test_client_execute_blocks_unlink_before_executor(self):
        executor = Mock(spec=OdooExecutor)
        client = OdooClient(executor=executor)
        with self.assertRaises(DeletionNotSupportedError):
            client.execute("res.partner", "unlink", [1, 2])
        # The guard fires before any delegation to the executor.
        executor.execute.assert_not_called()

    def test_recordset_execute_blocks_unlink_before_executor(self):
        executor = Mock(spec=OdooExecutor)
        recordset = OdooRecordset(executor, "res.partner", [1, 2])
        with self.assertRaises(DeletionNotSupportedError):
            recordset._execute("unlink", [1, 2])
        executor.execute.assert_not_called()

    def test_error_carries_canonical_message_and_method(self):
        with self.assertRaises(DeletionNotSupportedError) as ctx:
            forbid_unlink("unlink")
        message = str(ctx.exception)
        self.assertIn("purposefully not implemented", message)
        self.assertEqual(ctx.exception.method, "unlink")


if __name__ == "__main__":
    unittest.main()
