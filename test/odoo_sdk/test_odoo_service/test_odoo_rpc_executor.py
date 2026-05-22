import unittest
import xmlrpc.client
from unittest.mock import Mock, patch

from odoo_sdk.odoo_service.odoo_rpc_executor import OdooRpcExecutor


class TestOdooRpcExecutor(unittest.TestCase):
    @patch("odoo_sdk.odoo_service.odoo_rpc_executor.xmlrpc.client.ServerProxy")
    def test_uid_authenticates_once(self, mock_server_proxy: Mock) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = 7
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        self.assertEqual(executor.uid, 7)
        self.assertEqual(executor.uid, 7)
        common_proxy.authenticate.assert_called_once_with("db", "user", "pw", {})

    @patch("odoo_sdk.odoo_service.odoo_rpc_executor.xmlrpc.client.ServerProxy")
    def test_uid_raises_permission_error_when_auth_returns_false(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = False
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(PermissionError):
            _ = executor.uid
        with self.assertRaises(PermissionError):
            _ = executor.uid

        # Failed auth should not mark the executor authenticated.
        self.assertEqual(common_proxy.authenticate.call_count, 2)

    @patch("odoo_sdk.odoo_service.odoo_rpc_executor.xmlrpc.client.ServerProxy")
    def test_uid_accepts_numeric_zero_uid(self, mock_server_proxy: Mock) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = 0
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        self.assertEqual(executor.uid, 0)

    @patch("odoo_sdk.odoo_service.odoo_rpc_executor.xmlrpc.client.ServerProxy")
    def test_execute_propagates_permission_error_from_auth(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = False
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(PermissionError):
            executor.execute("res.partner", "search", [])

        object_proxy.execute_kw.assert_not_called()

    @patch("odoo_sdk.odoo_service.odoo_rpc_executor.xmlrpc.client.ServerProxy")
    def test_execute_wraps_fault_with_model_and_method(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = 12
        object_proxy.execute_kw.side_effect = xmlrpc.client.Fault(2, "Boom")
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaisesRegex(RuntimeError, "res.partner.search"):
            executor.execute("res.partner", "search", [])

    @patch("odoo_sdk.odoo_service.odoo_rpc_executor.xmlrpc.client.ServerProxy")
    def test_execute_surfaces_connection_error_during_auth(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.side_effect = OSError("network down")
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(ConnectionError):
            executor.execute("res.partner", "search", [])

        object_proxy.execute_kw.assert_not_called()
