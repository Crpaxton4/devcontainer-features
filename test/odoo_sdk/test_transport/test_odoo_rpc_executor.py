import unittest
import xmlrpc.client
from unittest.mock import Mock, patch

from odoo_sdk.transport.rpc import OdooRpcExecutor


class TestOdooRpcExecutor(unittest.TestCase):
    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_uid_authenticates_once(self, mock_server_proxy: Mock) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = 7
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        self.assertEqual(executor.uid, 7)
        self.assertEqual(executor.uid, 7)
        common_proxy.authenticate.assert_called_once_with("db", "user", "pw", {})

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_uid_caches_false_authentication_result(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = False
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        self.assertFalse(executor.uid)
        self.assertFalse(executor.uid)
        common_proxy.authenticate.assert_called_once_with("db", "user", "pw", {})

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_uid_returns_auth_result_without_conversion(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = "7"
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        self.assertEqual(executor.uid, "7")

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_execute_passes_through_authentication_fault(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        fault = xmlrpc.client.Fault(
            1,
            "odoo.exceptions.AccessDenied: bad login or password",
        )
        common_proxy.authenticate.side_effect = fault
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(xmlrpc.client.Fault) as caught:
            executor.execute("res.partner", "search", [])

        self.assertIs(caught.exception, fault)
        object_proxy.execute_kw.assert_not_called()

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_execute_passes_through_execute_fault(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = 12
        fault = xmlrpc.client.Fault(2, "Boom")
        object_proxy.execute_kw.side_effect = fault
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(xmlrpc.client.Fault) as caught:
            executor.execute("res.partner", "search", [])

        self.assertIs(caught.exception, fault)

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_execute_passes_through_transport_errors(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = 12
        error = xmlrpc.client.ProtocolError(
            "https://example.com/xmlrpc/2/object",
            502,
            "Bad Gateway",
            {},
        )
        object_proxy.execute_kw.side_effect = error
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(xmlrpc.client.ProtocolError) as caught:
            executor.execute("res.partner", "search", [])

        self.assertIs(caught.exception, error)

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_execute_passes_through_auth_transport_errors(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        error = OSError("network down")
        common_proxy.authenticate.side_effect = error
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(OSError) as caught:
            executor.execute("res.partner", "search", [])

        self.assertIs(caught.exception, error)
        object_proxy.execute_kw.assert_not_called()

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_execute_forwards_args_and_kwargs(self, mock_server_proxy: Mock) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = 12
        object_proxy.execute_kw.return_value = [{"id": 1}]
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        result = executor.execute(
            "res.partner", "search", [("active", "=", True)], limit=3
        )

        self.assertEqual(result, [{"id": 1}])
        object_proxy.execute_kw.assert_called_once_with(
            "db",
            12,
            "pw",
            "res.partner",
            "search",
            [[("active", "=", True)]],
            {"limit": 3},
        )
