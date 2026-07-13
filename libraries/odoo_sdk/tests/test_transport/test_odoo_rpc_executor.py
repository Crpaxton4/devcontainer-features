import http.client
import socket
import unittest
import xmlrpc.client
from unittest.mock import Mock, patch

from odoo_sdk.transport.errors import (
    OdooAuthenticationError,
    OdooServerError,
    OdooTransportError,
    OdooValidationError,
)
from odoo_sdk.transport.rpc import (
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    OdooRpcExecutor,
    _make_timeout_transport,
    _SafeTimeoutTransport,
    _TimeoutTransport,
)


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

        self.assertEqual(executor.uid, -1)
        self.assertEqual(executor.uid, -1)
        common_proxy.authenticate.assert_called_once_with("db", "user", "pw", {})

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_uid_returns_auth_result(self, mock_server_proxy: Mock) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = "7"
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        self.assertEqual(executor.uid, 7)

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_execute_maps_authentication_fault(self, mock_server_proxy: Mock) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        fault = xmlrpc.client.Fault(
            1,
            "odoo.exceptions.AccessDenied: bad login or password",
        )
        common_proxy.authenticate.side_effect = fault
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(OdooAuthenticationError) as caught:
            executor.execute("res.partner", "search", [])

        exc = caught.exception
        self.assertEqual(str(exc), "bad login or password")
        self.assertEqual(exc.fault_code, 1)
        self.assertEqual(
            exc.fault_string, "odoo.exceptions.AccessDenied: bad login or password"
        )
        self.assertIsNone(exc.model)
        self.assertEqual(exc.method, "authenticate")
        object_proxy.execute_kw.assert_not_called()

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_execute_maps_unmarked_execute_fault_to_server_error(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = 12
        object_proxy.execute_kw.side_effect = xmlrpc.client.Fault(2, "Boom")
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(OdooServerError) as caught:
            executor.execute("res.partner", "search", [])

        exc = caught.exception
        self.assertEqual(str(exc), "Boom")
        self.assertEqual(exc.fault_code, 2)
        self.assertEqual(exc.fault_string, "Boom")
        self.assertEqual(exc.model, "res.partner")
        self.assertEqual(exc.method, "search")

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_execute_maps_marked_execute_fault_to_validation_error(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = 12
        object_proxy.execute_kw.side_effect = xmlrpc.client.Fault(
            3,
            "odoo.exceptions.ValidationError: Name is required",
        )
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(OdooValidationError) as caught:
            executor.execute("res.partner", "create", {})

        exc = caught.exception
        self.assertEqual(str(exc), "Name is required")
        self.assertEqual(exc.fault_code, 3)
        self.assertEqual(exc.model, "res.partner")
        self.assertEqual(exc.method, "create")

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
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

        exc = caught.exception
        self.assertEqual(str(exc), "Transport error communicating with Odoo server")
        self.assertEqual(exc.model, "res.partner")
        self.assertEqual(exc.method, "search")
        self.assertNotIsInstance(exc, xmlrpc.client.ProtocolError)

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_execute_maps_socket_timeout_to_transport_error(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = 12
        object_proxy.execute_kw.side_effect = socket.timeout("timed out")
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(OdooTransportError) as caught:
            executor.execute("res.partner", "search", [])

        self.assertEqual(caught.exception.detail, "timed out")

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_execute_maps_http_exception_to_transport_error(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = 12
        object_proxy.execute_kw.side_effect = http.client.HTTPException("broken")
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(OdooTransportError) as caught:
            executor.execute("res.partner", "search", [])

        self.assertEqual(caught.exception.detail, "broken")

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_execute_maps_auth_os_error_to_transport_error(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.side_effect = OSError("network down")
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(OdooTransportError) as caught:
            executor.execute("res.partner", "search", [])

        exc = caught.exception
        self.assertEqual(str(exc), "Transport error communicating with Odoo server")
        self.assertEqual(exc.detail, "network down")
        self.assertIsNone(exc.model)
        self.assertEqual(exc.method, "authenticate")
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


class TestOdooRpcExecutorTimeout(unittest.TestCase):
    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_default_timeout_applied_to_both_proxies(
        self, mock_server_proxy: Mock
    ) -> None:
        OdooRpcExecutor("https://example.com", "db", "user", "pw")

        transports = [
            call.kwargs["transport"] for call in mock_server_proxy.call_args_list
        ]
        self.assertEqual(len(transports), 2)
        for transport in transports:
            self.assertIsInstance(transport, _SafeTimeoutTransport)
            self.assertEqual(transport._timeout, DEFAULT_REQUEST_TIMEOUT_SECONDS)

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_configured_timeout_applied_to_both_proxies(
        self, mock_server_proxy: Mock
    ) -> None:
        OdooRpcExecutor("https://example.com", "db", "user", "pw", timeout=3.0)

        transports = [
            call.kwargs["transport"] for call in mock_server_proxy.call_args_list
        ]
        for transport in transports:
            self.assertEqual(transport._timeout, 3.0)

    def test_https_url_selects_safe_transport(self) -> None:
        transport = _make_timeout_transport("https://example.com", 4.0)
        self.assertIsInstance(transport, _SafeTimeoutTransport)
        self.assertEqual(transport._timeout, 4.0)

    def test_http_url_selects_plain_transport(self) -> None:
        transport = _make_timeout_transport("http://example.com", 4.0)
        self.assertIsInstance(transport, _TimeoutTransport)
        self.assertNotIsInstance(transport, _SafeTimeoutTransport)
        self.assertEqual(transport._timeout, 4.0)

    def test_transport_applies_timeout_to_connection(self) -> None:
        transport = _TimeoutTransport(6.0)
        connection = transport.make_connection("example.com")
        self.assertEqual(connection.timeout, 6.0)

    def test_safe_transport_applies_timeout_to_connection(self) -> None:
        transport = _SafeTimeoutTransport(6.0)
        connection = transport.make_connection("example.com")
        self.assertEqual(connection.timeout, 6.0)
