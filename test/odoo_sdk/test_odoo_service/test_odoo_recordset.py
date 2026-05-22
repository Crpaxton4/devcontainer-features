from copy import deepcopy
import unittest
from unittest.mock import Mock

from odoo_sdk.odoo_service.odoo_env import OdooEnv
from odoo_sdk.odoo_service.odoo_executor import OdooExecutor
from odoo_sdk.odoo_service.odoo_recordset import OdooRecordset


class TestOdooRecordset(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = Mock(spec=OdooExecutor)
        self.env = OdooEnv(self.executor, {"lang": "en_US"})

    def test_identity_is_exposed_and_ids_are_immutable(self) -> None:
        source_ids = [3, 1, 2]

        recordset = OdooRecordset(self.env, "res.partner", source_ids)
        source_ids.append(9)

        self.assertIs(recordset.env, self.env)
        self.assertEqual(recordset.model_name, "res.partner")
        self.assertEqual(recordset.ids, (3, 1, 2))

    def test_read_returns_raw_rows_with_env_context(self) -> None:
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

    def test_read_returns_empty_rows_without_io_for_empty_ids(self) -> None:
        recordset = OdooRecordset(self.env, "res.partner", [])

        result = recordset.read(["name"])

        self.assertEqual(result, [])
        self.executor.execute.assert_not_called()

    def test_write_uses_env_context(self) -> None:
        self.executor.execute.return_value = True
        recordset = OdooRecordset(self.env, "res.partner", [4, 5])

        result = recordset.write({"name": "Updated"})

        self.assertTrue(result)
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "write",
            [4, 5],
            {"name": "Updated"},
            context={"lang": "en_US"},
        )

    def test_write_rejects_empty_ids_and_empty_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one id"):
            OdooRecordset(self.env, "res.partner", []).write({"name": "Updated"})

        with self.assertRaisesRegex(ValueError, "at least one value"):
            OdooRecordset(self.env, "res.partner", [1]).write({})

    def test_unlink_uses_env_context(self) -> None:
        self.executor.execute.return_value = True
        recordset = OdooRecordset(self.env, "res.partner", [4, 5])

        result = recordset.unlink()

        self.assertTrue(result)
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "unlink",
            [4, 5],
            context={"lang": "en_US"},
        )

    def test_unlink_rejects_empty_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one id"):
            OdooRecordset(self.env, "res.partner", []).unlink()

    def test_exists_preserves_input_order_for_surviving_ids(self) -> None:
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

    def test_browse_returns_recordset_for_same_model_and_env(self) -> None:
        recordset = OdooRecordset(self.env, "res.partner", [1])

        result = recordset.browse([9, 8])

        self.assertIsInstance(result, OdooRecordset)
        self.assertIs(result.env, self.env)
        self.assertEqual(result.model_name, "res.partner")
        self.assertEqual(result.ids, (9, 8))

    def test_search_returns_recordset_with_search_options_and_context(self) -> None:
        self.executor.execute.return_value = [8, 4]
        recordset = OdooRecordset(self.env, "res.partner", [99])

        result = recordset.search(
            [
                ("active", "=", True),
                "|",
                ("company_id", "=", 3),
                ("name", "ilike", "Acme"),
            ],
            limit=2,
            offset=1,
            order="name asc",
        )

        self.assertIsInstance(result, OdooRecordset)
        self.assertEqual(result.ids, (8, 4))
        self.executor.execute.assert_called_once_with(
            "res.partner",
            "search",
            [
                ("active", "=", True),
                "|",
                ("company_id", "=", 3),
                ("name", "ilike", "Acme"),
            ],
            context={"lang": "en_US"},
            limit=2,
            offset=1,
            order="name asc",
        )

    def test_with_context_returns_new_recordset_without_mutating_original(self) -> None:
        recordset = OdooRecordset(self.env, "res.partner", [1, 2])
        extra_context = {"tz": ["UTC"]}
        expected_extra_context = deepcopy(extra_context)

        derived = recordset.with_context(extra_context)
        extra_context["tz"].append("Europe/Brussels")

        self.assertIsNot(derived, recordset)
        self.assertEqual(recordset.env.context, {"lang": "en_US"})
        self.assertEqual(derived.env.context, {"lang": "en_US", **expected_extra_context})
        self.assertEqual(derived.ids, (1, 2))

    def test_construction_and_chaining_do_not_trigger_hidden_io(self) -> None:
        recordset = OdooRecordset(self.env, "res.partner", [1, 2])

        chained = recordset.browse([3]).with_context({"tz": "UTC"})

        self.assertEqual(chained.ids, (3,))
        self.executor.execute.assert_not_called()