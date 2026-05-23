import unittest
import xmlrpc.client
from unittest.mock import Mock, patch

from odoo_sdk.odoo_service import (
    OdooAccessError,
    OdooAuthenticationError,
    OdooMissingRecordError,
    OdooServerError,
    OdooTransportError,
    OdooValidationError,
)
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

        with self.assertRaises(OdooAuthenticationError) as first_error:
            _ = executor.uid
        with self.assertRaises(OdooAuthenticationError):
            _ = executor.uid

        # Failed auth should not mark the executor authenticated.
        self.assertEqual(common_proxy.authenticate.call_count, 2)
        self.assertEqual(first_error.exception.operation, "authenticate")
        self.assertNotIn("pw", str(first_error.exception))

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

        with self.assertRaises(OdooAuthenticationError):
            executor.execute("res.partner", "search", [])

        object_proxy.execute_kw.assert_not_called()

    @patch("odoo_sdk.odoo_service.odoo_rpc_executor.xmlrpc.client.ServerProxy")
    def test_uid_maps_fault_during_authentication_to_authentication_error(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.side_effect = xmlrpc.client.Fault(
            1, "odoo.exceptions.AccessDenied: bad login or password"
        )
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(OdooAuthenticationError) as caught:
            _ = executor.uid

        self.assertEqual(caught.exception.fault_code, 1)
        self.assertEqual(caught.exception.operation, "authenticate")
        self.assertEqual(
            caught.exception.fault_string,
            "odoo.exceptions.AccessDenied: bad login or password",
        )
        self.assertIsInstance(caught.exception.__cause__, xmlrpc.client.Fault)

    @patch("odoo_sdk.odoo_service.odoo_rpc_executor.xmlrpc.client.ServerProxy")
    def test_execute_maps_access_fault_with_model_and_method(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = 12
        object_proxy.execute_kw.side_effect = xmlrpc.client.Fault(
            2, "odoo.exceptions.AccessError: Access denied"
        )
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(OdooAccessError) as caught:
            executor.execute("res.partner", "search", [])

        self.assertEqual(str(caught.exception), "Odoo access denied (res.partner.search)")
        self.assertEqual(caught.exception.model, "res.partner")
        self.assertEqual(caught.exception.method, "search")
        self.assertEqual(caught.exception.operation, "res.partner.search")
        self.assertEqual(caught.exception.fault_code, 2)
        self.assertIsInstance(caught.exception.__cause__, xmlrpc.client.Fault)

    @patch("odoo_sdk.odoo_service.odoo_rpc_executor.xmlrpc.client.ServerProxy")
    def test_execute_maps_validation_fault(self, mock_server_proxy: Mock) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = 12
        object_proxy.execute_kw.side_effect = xmlrpc.client.Fault(
            3, "odoo.exceptions.ValidationError: Wrong value for name"
        )
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(OdooValidationError) as caught:
            executor.execute("res.partner", "write", [1], {"name": ""})

        self.assertEqual(caught.exception.fault_code, 3)
        self.assertEqual(caught.exception.operation, "res.partner.write")
        self.assertIsInstance(caught.exception.__cause__, xmlrpc.client.Fault)

    @patch("odoo_sdk.odoo_service.odoo_rpc_executor.xmlrpc.client.ServerProxy")
    def test_execute_maps_missing_record_fault(self, mock_server_proxy: Mock) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = 12
        object_proxy.execute_kw.side_effect = xmlrpc.client.Fault(
            4,
            "odoo.exceptions.MissingError: Record does not exist or has been deleted",
        )
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(OdooMissingRecordError) as caught:
            executor.execute("res.partner", "read", [1])

        self.assertEqual(caught.exception.fault_code, 4)
        self.assertEqual(caught.exception.operation, "res.partner.read")
        self.assertIsInstance(caught.exception.__cause__, xmlrpc.client.Fault)

    @patch("odoo_sdk.odoo_service.odoo_rpc_executor.xmlrpc.client.ServerProxy")
    def test_execute_maps_unclassified_fault_to_server_error(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = 12
        object_proxy.execute_kw.side_effect = xmlrpc.client.Fault(5, "Boom")
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(OdooServerError) as caught:
            executor.execute("res.partner", "search", [])

        self.assertEqual(caught.exception.fault_code, 5)
        self.assertEqual(caught.exception.fault_string, "Boom")
        self.assertIsInstance(caught.exception.__cause__, xmlrpc.client.Fault)

    @patch("odoo_sdk.odoo_service.odoo_rpc_executor.xmlrpc.client.ServerProxy")
    def test_execute_surfaces_connection_error_during_auth(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.side_effect = OSError("network down")
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(OdooTransportError) as caught:
            executor.execute("res.partner", "search", [])

        object_proxy.execute_kw.assert_not_called()
        self.assertEqual(caught.exception.operation, "authenticate")
        self.assertEqual(caught.exception.detail, "network down")
        self.assertNotIn("pw", str(caught.exception))

    @patch("odoo_sdk.odoo_service.odoo_rpc_executor.xmlrpc.client.ServerProxy")
    def test_execute_maps_protocol_error_to_transport_error(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = 12
        object_proxy.execute_kw.side_effect = xmlrpc.client.ProtocolError(
            "https://example.com/xmlrpc/2/object",
            502,
            "Bad Gateway",
            {},
        )
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(OdooTransportError) as caught:
            executor.execute("res.partner", "search", [])

        self.assertEqual(caught.exception.operation, "res.partner.search")
        self.assertEqual(caught.exception.model, "res.partner")
        self.assertEqual(caught.exception.method, "search")
        self.assertIn("Bad Gateway", caught.exception.detail or "")

    @patch("odoo_sdk.odoo_service.odoo_rpc_executor.xmlrpc.client.ServerProxy")
    def test_uid_maps_unexpected_auth_response_to_transport_error(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = "not-a-uid"
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(OdooTransportError) as caught:
            _ = executor.uid

        self.assertEqual(caught.exception.operation, "authenticate")
        self.assertEqual(
            caught.exception.detail,
            "Unexpected auth response from Odoo: 'not-a-uid'",
        )
