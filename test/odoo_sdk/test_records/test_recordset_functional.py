import unittest
from unittest.mock import Mock, call

from odoo_sdk.env.env import OdooEnv
from odoo_sdk.transport.executor import OdooExecutor
from odoo_sdk.records.recordset import OdooRecordset


class TestFilteredCallable(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = Mock(spec=OdooExecutor)
        self.env = OdooEnv(self.executor)

    def test_filtered_callable_keeps_matching_records(self) -> None:
        self.executor.execute.side_effect = [
            {"active": {"type": "boolean"}},
            [{"id": 7, "active": True}, {"id": 8, "active": False}],
        ]
        rs = OdooRecordset(self.env, "res.partner", [7, 8])

        result = rs.filtered(lambda r: r.active)

        self.assertIsInstance(result, OdooRecordset)
        self.assertEqual(result.ids, (7,))

    def test_filtered_callable_empty_recordset_returns_empty(self) -> None:
        rs = OdooRecordset(self.env, "res.partner", [])

        result = rs.filtered(lambda r: True)

        self.assertEqual(result.ids, ())
        self.executor.execute.assert_not_called()

    def test_filtered_callable_no_matches_returns_empty(self) -> None:
        self.executor.execute.side_effect = [
            {"active": {"type": "boolean"}},
            [{"id": 7, "active": False}, {"id": 8, "active": False}],
        ]
        rs = OdooRecordset(self.env, "res.partner", [7, 8])

        result = rs.filtered(lambda r: r.active)

        self.assertEqual(result.ids, ())

    def test_filtered_callable_all_match_returns_all(self) -> None:
        self.executor.execute.side_effect = [
            {"active": {"type": "boolean"}},
            [{"id": 7, "active": True}, {"id": 8, "active": True}],
        ]
        rs = OdooRecordset(self.env, "res.partner", [7, 8])

        result = rs.filtered(lambda r: r.active)

        self.assertEqual(result.ids, (7, 8))

    def test_filtered_preserves_prefetch_ids(self) -> None:
        self.executor.execute.side_effect = [
            {"active": {"type": "boolean"}},
            [{"id": 7, "active": True}, {"id": 8, "active": False}],
        ]
        rs = OdooRecordset(self.env, "res.partner", [7, 8])

        result = rs.filtered(lambda r: r.active)

        self.assertEqual(result._prefetch_ids, rs._prefetch_ids)

    def test_filtered_invalid_type_raises_type_error(self) -> None:
        rs = OdooRecordset(self.env, "res.partner", [7])
        with self.assertRaises(TypeError):
            rs.filtered(123)


class TestFilteredString(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = Mock(spec=OdooExecutor)
        self.env = OdooEnv(self.executor)

    def test_filtered_string_single_field_truthy(self) -> None:
        self.executor.execute.side_effect = [
            {"active": {"type": "boolean"}},
            [{"id": 7, "active": True}, {"id": 8, "active": False}],
        ]
        rs = OdooRecordset(self.env, "res.partner", [7, 8])

        result = rs.filtered("active")

        self.assertEqual(result.ids, (7,))

    def test_filtered_string_dotted_path(self) -> None:
        # partner_id → many2one → then is_company
        self.executor.execute.side_effect = [
            # first record [7]: partner_id fetch
            {"partner_id": {"type": "many2one", "relation": "res.partner"}},
            [{"id": 7, "partner_id": [3, "Acme"]}, {"id": 8, "partner_id": False}],
            # is_company on partner 3
            {"is_company": {"type": "boolean"}},
            [{"id": 3, "is_company": True}],
        ]
        rs = OdooRecordset(self.env, "res.sale.order", [7, 8])

        result = rs.filtered("partner_id.is_company")

        self.assertEqual(result.ids, (7,))

    def test_filtered_string_null_intermediate_excluded(self) -> None:
        # Both records have no partner_id
        self.executor.execute.side_effect = [
            {"partner_id": {"type": "many2one", "relation": "res.partner"}},
            [{"id": 7, "partner_id": False}, {"id": 8, "partner_id": False}],
        ]
        rs = OdooRecordset(self.env, "res.sale.order", [7, 8])

        result = rs.filtered("partner_id.is_company")

        self.assertEqual(result.ids, ())


class TestFilteredDomain(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = Mock(spec=OdooExecutor)
        self.env = OdooEnv(self.executor)

    def test_filtered_with_domain_list(self) -> None:
        self.executor.execute.side_effect = [
            {"active": {"type": "boolean"}},
            [{"id": 7, "active": True}, {"id": 8, "active": False}],
        ]
        rs = OdooRecordset(self.env, "res.partner", [7, 8])

        result = rs.filtered([("active", "=", True)])

        self.assertEqual(result.ids, (7,))

    def test_filtered_with_domain_expression(self) -> None:
        from odoo_sdk.query.domain import DomainExpression

        self.executor.execute.side_effect = [
            {"active": {"type": "boolean"}},
            [{"id": 7, "active": True}, {"id": 8, "active": False}],
        ]
        rs = OdooRecordset(self.env, "res.partner", [7, 8])
        domain = DomainExpression.normalize([("active", "=", True)])

        result = rs.filtered(domain)

        self.assertEqual(result.ids, (7,))


class TestFilteredDomainMethod(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = Mock(spec=OdooExecutor)
        self.env = OdooEnv(self.executor)

    def test_filtered_domain_single_condition(self) -> None:
        self.executor.execute.side_effect = [
            {"active": {"type": "boolean"}},
            [{"id": 7, "active": True}, {"id": 8, "active": False}],
        ]
        rs = OdooRecordset(self.env, "res.partner", [7, 8])

        result = rs.filtered_domain([("active", "=", True)])

        self.assertEqual(result.ids, (7,))
        self.assertIsInstance(result, OdooRecordset)

    def test_filtered_domain_empty_domain_returns_all(self) -> None:
        rs = OdooRecordset(self.env, "res.partner", [7, 8])

        result = rs.filtered_domain([])

        self.assertEqual(result.ids, (7, 8))
        self.executor.execute.assert_not_called()

    def test_filtered_domain_empty_recordset_returns_empty(self) -> None:
        rs = OdooRecordset(self.env, "res.partner", [])

        result = rs.filtered_domain([("active", "=", True)])

        self.assertEqual(result.ids, ())
        self.executor.execute.assert_not_called()

    def test_filtered_domain_or_condition(self) -> None:
        self.executor.execute.side_effect = [
            {"state": {"type": "char"}},
            [
                {"id": 1, "state": "draft"},
                {"id": 2, "state": "done"},
                {"id": 3, "state": "cancel"},
            ],
        ]
        rs = OdooRecordset(self.env, "sale.order", [1, 2, 3])

        result = rs.filtered_domain(
            ["|", ("state", "=", "draft"), ("state", "=", "done")]
        )

        self.assertEqual(result.ids, (1, 2))

    def test_filtered_domain_cached_fields_no_extra_rpc(self) -> None:
        # Prime cache manually then filtered_domain should not call execute again
        self.executor.execute.side_effect = [
            {"active": {"type": "boolean"}},
            [{"id": 7, "active": True}, {"id": 8, "active": False}],
        ]
        rs = OdooRecordset(self.env, "res.partner", [7, 8])
        # Access field to prime cache
        list(rs)[0].active  # fetches for both 7 and 8 via prefetch
        call_count_after_prime = self.executor.execute.call_count

        result = rs.filtered_domain([("active", "=", True)])

        self.assertEqual(result.ids, (7,))
        # No additional RPC calls for already-cached fields
        self.assertEqual(self.executor.execute.call_count, call_count_after_prime)

    def test_filtered_domain_unknown_field_raises_attribute_error(self) -> None:
        self.executor.execute.return_value = {}
        rs = OdooRecordset(self.env, "res.partner", [7])

        with self.assertRaises(AttributeError):
            rs.filtered_domain([("nonexistent_field", "=", True)])

    def test_filtered_domain_preserves_prefetch_ids(self) -> None:
        self.executor.execute.side_effect = [
            {"active": {"type": "boolean"}},
            [{"id": 7, "active": True}, {"id": 8, "active": False}],
        ]
        rs = OdooRecordset(self.env, "res.partner", [7, 8])

        result = rs.filtered_domain([("active", "=", True)])

        self.assertEqual(result._prefetch_ids, rs._prefetch_ids)


class TestMapped(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = Mock(spec=OdooExecutor)
        self.env = OdooEnv(self.executor)

    def test_mapped_callable_returns_list(self) -> None:
        self.executor.execute.side_effect = [
            {"amount_total": {"type": "float"}},
            [{"id": 7, "amount_total": 10.0}, {"id": 8, "amount_total": 20.0}],
        ]
        rs = OdooRecordset(self.env, "sale.order", [7, 8])

        result = rs.mapped(lambda r: r.amount_total * 2)

        self.assertEqual(result, [20.0, 40.0])

    def test_mapped_scalar_field_returns_list_of_values(self) -> None:
        self.executor.execute.side_effect = [
            {"name": {"type": "char"}},
            [{"id": 7, "name": "Alpha"}, {"id": 8, "name": "Beta"}],
        ]
        rs = OdooRecordset(self.env, "res.partner", [7, 8])

        result = rs.mapped("name")

        self.assertEqual(result, ["Alpha", "Beta"])

    def test_mapped_empty_recordset_returns_empty_list(self) -> None:
        rs = OdooRecordset(self.env, "res.partner", [])

        result = rs.mapped("name")

        self.assertEqual(result, [])
        self.executor.execute.assert_not_called()

    def test_mapped_relational_field_returns_deduped_recordset(self) -> None:
        self.executor.execute.side_effect = [
            # metadata for partner_id
            {"partner_id": {"type": "many2one", "relation": "res.partner"}},
            # read for orders 7 and 8
            [
                {"id": 7, "partner_id": [3, "Acme"]},
                {"id": 8, "partner_id": [3, "Acme"]},  # same partner
            ],
        ]
        rs = OdooRecordset(self.env, "sale.order", [7, 8])

        result = rs.mapped("partner_id")

        self.assertIsInstance(result, OdooRecordset)
        self.assertEqual(result.model_name, "res.partner")
        # deduped: partner 3 appears only once
        self.assertEqual(result.ids, (3,))

    def test_mapped_relational_field_empty_recordset_returns_empty_list(self) -> None:
        rs = OdooRecordset(self.env, "sale.order", [])

        result = rs.mapped("partner_id")

        self.assertEqual(result, [])
        self.executor.execute.assert_not_called()

    def test_mapped_dotted_path_scalar_terminal(self) -> None:
        self.executor.execute.side_effect = [
            # metadata for partner_id on sale.order
            {"partner_id": {"type": "many2one", "relation": "res.partner"}},
            # read sale.order fields
            [
                {"id": 7, "partner_id": [3, "Acme"]},
                {"id": 8, "partner_id": [5, "Beta"]},
            ],
            # metadata for name on res.partner
            {"name": {"type": "char"}},
            # read partner names
            [{"id": 3, "name": "Acme"}, {"id": 5, "name": "Beta"}],
        ]
        rs = OdooRecordset(self.env, "sale.order", [7, 8])

        result = rs.mapped("partner_id.name")

        self.assertEqual(result, ["Acme", "Beta"])

    def test_mapped_callable_empty_returns_empty_list(self) -> None:
        rs = OdooRecordset(self.env, "res.partner", [])

        result = rs.mapped(lambda r: r.name)

        self.assertEqual(result, [])
        self.executor.execute.assert_not_called()

    def test_mapped_invalid_type_raises_type_error(self) -> None:
        rs = OdooRecordset(self.env, "res.partner", [7])
        with self.assertRaises(TypeError):
            rs.mapped(42)


class TestSorted(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = Mock(spec=OdooExecutor)
        self.env = OdooEnv(self.executor)

    def test_sorted_none_key_sorts_by_id(self) -> None:
        rs = OdooRecordset(self.env, "res.partner", [8, 3, 5])

        result = rs.sorted()

        self.assertEqual(result.ids, (3, 5, 8))
        self.executor.execute.assert_not_called()

    def test_sorted_none_key_reverse(self) -> None:
        rs = OdooRecordset(self.env, "res.partner", [3, 8, 5])

        result = rs.sorted(reverse=True)

        self.assertEqual(result.ids, (8, 5, 3))

    def test_sorted_callable_key(self) -> None:
        self.executor.execute.side_effect = [
            {"name": {"type": "char"}},
            [
                {"id": 7, "name": "Charlie"},
                {"id": 8, "name": "Alice"},
                {"id": 9, "name": "Bob"},
            ],
        ]
        rs = OdooRecordset(self.env, "res.partner", [7, 8, 9])

        result = rs.sorted(key=lambda r: r.name)

        self.assertEqual(result.ids, (8, 9, 7))  # Alice, Bob, Charlie

    def test_sorted_callable_reverse(self) -> None:
        self.executor.execute.side_effect = [
            {"name": {"type": "char"}},
            [
                {"id": 7, "name": "Charlie"},
                {"id": 8, "name": "Alice"},
                {"id": 9, "name": "Bob"},
            ],
        ]
        rs = OdooRecordset(self.env, "res.partner", [7, 8, 9])

        result = rs.sorted(key=lambda r: r.name, reverse=True)

        self.assertEqual(result.ids, (7, 9, 8))  # Charlie, Bob, Alice

    def test_sorted_string_field_asc(self) -> None:
        self.executor.execute.side_effect = [
            {"name": {"type": "char"}},
            [
                {"id": 7, "name": "Charlie"},
                {"id": 8, "name": "Alice"},
                {"id": 9, "name": "Bob"},
            ],
        ]
        rs = OdooRecordset(self.env, "res.partner", [7, 8, 9])

        result = rs.sorted("name")

        self.assertEqual(result.ids, (8, 9, 7))  # Alice, Bob, Charlie

    def test_sorted_string_field_desc(self) -> None:
        self.executor.execute.side_effect = [
            {"name": {"type": "char"}},
            [
                {"id": 7, "name": "Charlie"},
                {"id": 8, "name": "Alice"},
                {"id": 9, "name": "Bob"},
            ],
        ]
        rs = OdooRecordset(self.env, "res.partner", [7, 8, 9])

        result = rs.sorted("name DESC")

        self.assertEqual(result.ids, (7, 9, 8))  # Charlie, Bob, Alice

    def test_sorted_nulls_first(self) -> None:
        self.executor.execute.side_effect = [
            {"name": {"type": "char"}},
            [
                {"id": 7, "name": "Alice"},
                {"id": 8, "name": False},
                {"id": 9, "name": "Bob"},
            ],
        ]
        rs = OdooRecordset(self.env, "res.partner", [7, 8, 9])

        result = rs.sorted("name NULLS FIRST")

        # null (id 8) should come first, then Alice, Bob
        self.assertEqual(result.ids[0], 8)
        remaining = list(result.ids[1:])
        self.assertIn(7, remaining)
        self.assertIn(9, remaining)

    def test_sorted_nulls_last(self) -> None:
        self.executor.execute.side_effect = [
            {"name": {"type": "char"}},
            [
                {"id": 7, "name": "Alice"},
                {"id": 8, "name": False},
                {"id": 9, "name": "Bob"},
            ],
        ]
        rs = OdooRecordset(self.env, "res.partner", [7, 8, 9])

        result = rs.sorted("name NULLS LAST")

        self.assertEqual(result.ids[-1], 8)  # null last

    def test_sorted_empty_recordset_returns_empty(self) -> None:
        rs = OdooRecordset(self.env, "res.partner", [])

        result = rs.sorted("name")

        self.assertEqual(result.ids, ())
        self.executor.execute.assert_not_called()

    def test_sorted_preserves_prefetch_ids(self) -> None:
        rs = OdooRecordset(self.env, "res.partner", [8, 3, 5])

        result = rs.sorted()

        self.assertEqual(result._prefetch_ids, rs._prefetch_ids)

    def test_sorted_invalid_key_raises_type_error(self) -> None:
        rs = OdooRecordset(self.env, "res.partner", [7])
        with self.assertRaises(TypeError):
            rs.sorted(key=42)

    def test_sorted_no_extra_rpc_when_cached(self) -> None:
        self.executor.execute.side_effect = [
            {"name": {"type": "char"}},
            [{"id": 7, "name": "Charlie"}, {"id": 8, "name": "Alice"}],
        ]
        rs = OdooRecordset(self.env, "res.partner", [7, 8])
        # Prime cache
        list(rs)[0].name
        call_count = self.executor.execute.call_count

        rs.sorted("name")

        self.assertEqual(self.executor.execute.call_count, call_count)


class TestGrouped(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = Mock(spec=OdooExecutor)
        self.env = OdooEnv(self.executor)

    def test_grouped_by_string_field(self) -> None:
        self.executor.execute.side_effect = [
            {"state": {"type": "char"}},
            [
                {"id": 1, "state": "draft"},
                {"id": 2, "state": "done"},
                {"id": 3, "state": "draft"},
            ],
        ]
        rs = OdooRecordset(self.env, "sale.order", [1, 2, 3])

        result = rs.grouped("state")

        self.assertIn("draft", result)
        self.assertIn("done", result)
        self.assertEqual(result["draft"].ids, (1, 3))
        self.assertEqual(result["done"].ids, (2,))

    def test_grouped_by_callable(self) -> None:
        self.executor.execute.side_effect = [
            {"amount_total": {"type": "float"}},
            [
                {"id": 1, "amount_total": 100.0},
                {"id": 2, "amount_total": 200.0},
                {"id": 3, "amount_total": 100.0},
            ],
        ]
        rs = OdooRecordset(self.env, "sale.order", [1, 2, 3])

        result = rs.grouped(lambda r: r.amount_total > 150)

        self.assertIn(False, result)
        self.assertIn(True, result)
        self.assertEqual(result[False].ids, (1, 3))
        self.assertEqual(result[True].ids, (2,))

    def test_grouped_empty_recordset_returns_empty_dict(self) -> None:
        rs = OdooRecordset(self.env, "sale.order", [])

        result = rs.grouped("state")

        self.assertEqual(result, {})
        self.executor.execute.assert_not_called()

    def test_grouped_preserves_prefetch_ids_on_returned_recordsets(self) -> None:
        self.executor.execute.side_effect = [
            {"state": {"type": "char"}},
            [{"id": 1, "state": "draft"}, {"id": 2, "state": "done"}],
        ]
        rs = OdooRecordset(self.env, "sale.order", [1, 2])

        result = rs.grouped("state")

        for sub_rs in result.values():
            self.assertEqual(sub_rs._prefetch_ids, rs._prefetch_ids)

    def test_grouped_invalid_key_raises_type_error(self) -> None:
        rs = OdooRecordset(self.env, "res.partner", [7])
        with self.assertRaises(TypeError):
            rs.grouped(42)

    def test_grouped_preserves_insertion_order(self) -> None:
        self.executor.execute.side_effect = [
            {"state": {"type": "char"}},
            [
                {"id": 1, "state": "draft"},
                {"id": 2, "state": "done"},
                {"id": 3, "state": "cancel"},
            ],
        ]
        rs = OdooRecordset(self.env, "sale.order", [1, 2, 3])

        result = rs.grouped("state")

        self.assertEqual(list(result.keys()), ["draft", "done", "cancel"])
