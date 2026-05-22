import unittest
from unittest.mock import Mock

from odoo_sdk.odoo_service.odoo_env import OdooEnv
from odoo_sdk.odoo_service.odoo_executor import OdooExecutor
from odoo_sdk.odoo_service.odoo_recordset import OdooRecordset


class TestOdooRecordset(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = Mock(spec=OdooExecutor)
        self.env = OdooEnv(self.executor, {"lang": "en_US"})

    def test_identity_preserves_model_env_and_ordered_ids(self) -> None:
        input_ids = [3, 1, 2]

        recordset = OdooRecordset(self.env, "res.partner", input_ids)
        input_ids.append(4)

        self.assertIs(recordset.env, self.env)
        self.assertEqual(recordset.model_name, "res.partner")
        self.assertEqual(recordset.ids, (3, 1, 2))

    def test_read_returns_empty_rows_without_io_for_empty_ids(self) -> None:
        recordset = OdooRecordset(self.env, "res.partner", [])

        result = recordset.read(["name"])

        self.assertEqual(result, [])
        self.executor.execute.assert_not_called()

    def test_read_materializes_raw_rows_with_context(self) -> None:
        self.executor.execute.return_value = [{"id": 7, "name": "Acme"}]
        recordset = OdooRecordset(self.env, "res.partner", [7])

        result = recordset.read(["name"])

        self.assertEqual(result, [{"id": 7, "name": "Acme"}])
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "read",
            [7],
            context={"lang": "en_US"},
            fields=["name"],
        )

    def test_write_updates_current_ids_with_context(self) -> None:
        self.executor.execute.return_value = True
        recordset = OdooRecordset(self.env, "res.partner", [7, 8])

        result = recordset.write({"comment": "Updated"})

        self.assertTrue(result)
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "write",
            [7, 8],
            {"comment": "Updated"},
            context={"lang": "en_US"},
        )

    def test_write_rejects_empty_ids_and_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one id"):
            OdooRecordset(self.env, "res.partner", []).write({"name": "Acme"})

        with self.assertRaisesRegex(ValueError, "at least one value"):
            OdooRecordset(self.env, "res.partner", [1]).write({})

    def test_unlink_deletes_current_ids_with_context(self) -> None:
        self.executor.execute.return_value = True
        recordset = OdooRecordset(self.env, "res.partner", [7, 8])

        result = recordset.unlink()

        self.assertTrue(result)
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "unlink",
            [7, 8],
            context={"lang": "en_US"},
        )

    def test_unlink_rejects_empty_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one id"):
            OdooRecordset(self.env, "res.partner", []).unlink()

    def test_exists_preserves_surviving_id_order(self) -> None:
        self.executor.execute.return_value = [2, 1]
        recordset = OdooRecordset(self.env, "res.partner", [3, 1, 2])

        result = recordset.exists()

        self.assertIsInstance(result, OdooRecordset)
        self.assertEqual(result.ids, (1, 2))
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "search",
            [("id", "in", [3, 1, 2])],
            context={"lang": "en_US"},
        )

    def test_exists_returns_empty_recordset_without_io_for_empty_ids(self) -> None:
        recordset = OdooRecordset(self.env, "res.partner", [])

        result = recordset.exists()

        self.assertEqual(result.ids, ())
        self.executor.execute.assert_not_called()

    def test_browse_returns_same_model_recordset_without_io(self) -> None:
        recordset = OdooRecordset(self.env, "res.partner", [7])

        result = recordset.browse([9, 10])

        self.assertIsInstance(result, OdooRecordset)
        self.assertEqual(result.ids, (9, 10))
        self.assertEqual(result.model_name, "res.partner")
        self.assertIs(result.env, self.env)
        self.executor.execute.assert_not_called()

    def test_search_returns_new_recordset_with_domain_options_and_context(self) -> None:
        self.executor.execute.return_value = [4, 5]
        recordset = OdooRecordset(self.env, "res.partner", [99])

        result = recordset.search(
            [("active", "=", True)],
            limit=2,
            offset=1,
            order="name asc",
        )

        self.assertIsInstance(result, OdooRecordset)
        self.assertEqual(result.ids, (4, 5))
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "search",
            [("active", "=", True)],
            context={"lang": "en_US"},
            limit=2,
            offset=1,
            order="name asc",
        )

    def test_with_context_returns_new_recordset_without_mutating_original(self) -> None:
        recordset = OdooRecordset(self.env, "res.partner", [7, 8])

        derived = recordset.with_context({"tz": "UTC"})

        self.assertIsNot(derived, recordset)
        self.assertEqual(recordset.env.context, {"lang": "en_US"})
        self.assertEqual(derived.env.context, {"lang": "en_US", "tz": "UTC"})
        self.assertEqual(derived.ids, (7, 8))
        self.assertEqual(derived.model_name, "res.partner")
        self.executor.execute.assert_not_called()