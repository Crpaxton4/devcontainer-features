import http.client
import socket
import threading
import time
import unittest
import xmlrpc.client
from unittest.mock import Mock, patch

from odoo_sdk.state.config import OdooConnectionSettings
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
    def test_uid_concurrent_first_access_authenticates_once(
        self, mock_server_proxy: Mock
    ) -> None:
        # Regression test for the check-then-lock race: with the uid cache only
        # checked OUTSIDE the lock, every thread that queued behind the winner
        # would re-run the login handshake on wake-up. The slow authenticate stub
        # widens the race window so all threads reach the lock while the first
        # handshake is still in flight; double-checked locking must collapse them
        # into exactly one authenticate call.
        common_proxy = Mock()
        object_proxy = Mock()

        def slow_authenticate(*args: object) -> int:
            time.sleep(0.05)
            return 7

        common_proxy.authenticate.side_effect = slow_authenticate
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        thread_count = 8
        start_line = threading.Barrier(thread_count)
        results: list[int] = []
        errors: list[BaseException] = []

        def read_uid() -> None:
            try:
                start_line.wait(timeout=5.0)
                results.append(executor.uid)
            except BaseException as exc:  # pragma: no cover - defensive capture
                errors.append(exc)

        threads = [threading.Thread(target=read_uid) for _ in range(thread_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10.0)

        self.assertEqual(errors, [])
        self.assertEqual(results, [7] * thread_count)
        common_proxy.authenticate.assert_called_once_with("db", "user", "pw", {})

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_uid_raises_on_false_authentication(self, mock_server_proxy: Mock) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = False
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "secret-pw")

        with self.assertRaises(OdooAuthenticationError) as caught:
            _ = executor.uid

        exc = caught.exception
        self.assertTrue(
            str(exc).startswith(
                "Odoo authentication failed for user 'user' on database 'db'"
            )
        )
        self.assertIn("db", str(exc))
        self.assertIn("user", str(exc))
        self.assertNotIn("secret-pw", str(exc))
        self.assertEqual(exc.operation, "authenticate")
        self.assertIsNone(exc.model)
        self.assertEqual(exc.method, "authenticate")

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_uid_raises_on_zero_authentication(self, mock_server_proxy: Mock) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = 0
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(OdooAuthenticationError):
            _ = executor.uid

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_uid_raises_on_non_int_authentication(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = None
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(OdooAuthenticationError):
            _ = executor.uid

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_uid_raises_on_true_authentication(self, mock_server_proxy: Mock) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = True
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(OdooAuthenticationError) as caught:
            _ = executor.uid

        self.assertTrue(
            str(caught.exception).startswith(
                "Odoo authentication failed for user 'user' on database 'db'"
            )
        )

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_uid_does_not_cache_failed_authentication(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.side_effect = [False, 7]
        mock_server_proxy.side_effect = [common_proxy, object_proxy]

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(OdooAuthenticationError):
            _ = executor.uid

        self.assertEqual(executor.uid, 7)
        self.assertEqual(common_proxy.authenticate.call_count, 2)

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
        self.assertTrue(str(exc).startswith("bad login or password"))
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


def _fresh_settings(**overrides: object) -> OdooConnectionSettings:
    """Build refreshed connection settings matching the test executor's defaults."""
    values: dict = {
        "url": "https://example.com",
        "db": "db",
        "username": "user",
        "password": "pw",
    }
    values.update(overrides)
    return OdooConnectionSettings(**values)


def _route_proxies(common_proxy: Mock, object_proxy: Mock):
    """Return a ServerProxy side effect routing each endpoint to a fixed mock.

    Rebuilt proxies (after a settings change) resolve to the same mocks, so one
    ``authenticate`` / ``execute_kw`` stub spans both proxy generations.
    """

    def route(url: str, transport: object = None) -> Mock:
        return common_proxy if url.endswith("/common") else object_proxy

    return route


class TestOdooRpcExecutorCredentialRefresh(unittest.TestCase):
    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_uid_retries_once_when_refresh_rotates_password(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.side_effect = lambda db, user, password, ctx: (
            7 if password == "new" else False
        )
        mock_server_proxy.side_effect = _route_proxies(common_proxy, object_proxy)
        refresh = Mock(return_value=_fresh_settings(password="new"))

        executor = OdooRpcExecutor(
            "https://example.com", "db", "user", "old", credentials_refresh=refresh
        )

        self.assertEqual(executor.uid, 7)
        self.assertEqual(common_proxy.authenticate.call_count, 2)
        self.assertEqual(
            common_proxy.authenticate.call_args_list[1].args,
            ("db", "user", "new", {}),
        )
        refresh.assert_called_once_with()

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_uid_second_failure_after_rotation_propagates_plain(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = False
        mock_server_proxy.side_effect = _route_proxies(common_proxy, object_proxy)
        refresh = Mock(return_value=_fresh_settings(password="new"))

        executor = OdooRpcExecutor(
            "https://example.com", "db", "user", "old", credentials_refresh=refresh
        )

        with self.assertRaises(OdooAuthenticationError) as caught:
            _ = executor.uid

        self.assertEqual(common_proxy.authenticate.call_count, 2)
        self.assertEqual(
            str(caught.exception),
            "Odoo authentication failed for user 'user' on database 'db'",
        )
        self.assertNotIn("recently rotated", str(caught.exception))

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_uid_unchanged_settings_raise_hinted_error_without_retry(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = False
        mock_server_proxy.side_effect = _route_proxies(common_proxy, object_proxy)
        refresh = Mock(return_value=_fresh_settings())

        executor = OdooRpcExecutor(
            "https://example.com", "db", "user", "pw", credentials_refresh=refresh
        )

        with self.assertRaises(OdooAuthenticationError) as caught:
            _ = executor.uid

        common_proxy.authenticate.assert_called_once_with("db", "user", "pw", {})
        self.assertIn("recently rotated", str(caught.exception))
        refresh.assert_called_once_with()

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_uid_without_refresh_hook_fails_once_with_hint(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = False
        mock_server_proxy.side_effect = _route_proxies(common_proxy, object_proxy)

        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")

        with self.assertRaises(OdooAuthenticationError) as caught:
            _ = executor.uid

        common_proxy.authenticate.assert_called_once_with("db", "user", "pw", {})
        self.assertIn("recently rotated", str(caught.exception))
        self.assertIn("restart the process", str(caught.exception))

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_uid_refresh_error_surfaces_original_hinted_error(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = False
        mock_server_proxy.side_effect = _route_proxies(common_proxy, object_proxy)
        refresh = Mock(side_effect=ValueError("config file is now invalid"))

        executor = OdooRpcExecutor(
            "https://example.com", "db", "user", "pw", credentials_refresh=refresh
        )

        with self.assertRaises(OdooAuthenticationError) as caught:
            _ = executor.uid

        common_proxy.authenticate.assert_called_once_with("db", "user", "pw", {})
        self.assertTrue(
            str(caught.exception).startswith(
                "Odoo authentication failed for user 'user' on database 'db'"
            )
        )
        self.assertIn("recently rotated", str(caught.exception))

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_uid_url_change_rebuilds_proxies_with_new_url(
        self, mock_server_proxy: Mock
    ) -> None:
        stale_common = Mock()
        stale_object = Mock()
        fresh_common = Mock()
        fresh_object = Mock()
        stale_common.authenticate.return_value = False
        fresh_common.authenticate.return_value = 9
        mock_server_proxy.side_effect = [
            stale_common,
            stale_object,
            fresh_common,
            fresh_object,
        ]
        refresh = Mock(return_value=_fresh_settings(url="https://new.example.com"))

        executor = OdooRpcExecutor(
            "https://example.com", "db", "user", "pw", credentials_refresh=refresh
        )

        self.assertEqual(executor.uid, 9)
        urls = [call.args[0] for call in mock_server_proxy.call_args_list]
        self.assertEqual(
            urls,
            [
                "https://example.com/xmlrpc/2/common",
                "https://example.com/xmlrpc/2/object",
                "https://new.example.com/xmlrpc/2/common",
                "https://new.example.com/xmlrpc/2/object",
            ],
        )

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_execute_retries_once_after_access_denied_with_rotated_password(
        self, mock_server_proxy: Mock
    ) -> None:
        # The issue's live failure mode: uid cached from an earlier login, then
        # the server-side rotation makes execute_kw fault with AccessDenied.
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.side_effect = lambda db, user, password, ctx: (
            5 if password == "new" else 3
        )
        object_proxy.execute_kw.side_effect = [
            xmlrpc.client.Fault(4, "odoo.exceptions.AccessDenied: Access Denied"),
            [{"id": 1}],
        ]
        mock_server_proxy.side_effect = _route_proxies(common_proxy, object_proxy)
        refresh = Mock(return_value=_fresh_settings(password="new"))

        executor = OdooRpcExecutor(
            "https://example.com", "db", "user", "old", credentials_refresh=refresh
        )

        result = executor.execute("res.partner", "search", [])

        self.assertEqual(result, [{"id": 1}])
        self.assertEqual(object_proxy.execute_kw.call_count, 2)
        retry_args = object_proxy.execute_kw.call_args_list[1].args
        self.assertEqual(retry_args[1], 5)
        self.assertEqual(retry_args[2], "new")
        refresh.assert_called_once_with()

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_execute_unchanged_settings_raise_hinted_error_and_drop_uid(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = 3
        object_proxy.execute_kw.side_effect = xmlrpc.client.Fault(
            4, "odoo.exceptions.AccessDenied: Access Denied"
        )
        mock_server_proxy.side_effect = _route_proxies(common_proxy, object_proxy)
        refresh = Mock(return_value=_fresh_settings())

        executor = OdooRpcExecutor(
            "https://example.com", "db", "user", "pw", credentials_refresh=refresh
        )

        with self.assertRaises(OdooAuthenticationError) as caught:
            executor.execute("res.partner", "search", [])

        self.assertEqual(object_proxy.execute_kw.call_count, 1)
        self.assertIn("recently rotated", str(caught.exception))
        # The stale uid is dropped so the next call re-authenticates.
        self.assertIsNone(executor._uid)
        refresh.assert_called_once_with()

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_execute_validation_fault_is_not_retried_and_skips_refresh(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = 3
        object_proxy.execute_kw.side_effect = xmlrpc.client.Fault(
            3, "odoo.exceptions.ValidationError: Name is required"
        )
        mock_server_proxy.side_effect = _route_proxies(common_proxy, object_proxy)
        refresh = Mock(return_value=_fresh_settings(password="new"))

        executor = OdooRpcExecutor(
            "https://example.com", "db", "user", "pw", credentials_refresh=refresh
        )

        with self.assertRaises(OdooValidationError):
            executor.execute("res.partner", "create", {})

        self.assertEqual(object_proxy.execute_kw.call_count, 1)
        refresh.assert_not_called()
