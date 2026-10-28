import unittest
from collections.abc import Mapping
from unittest.mock import Mock, patch

from hypothesis import given, strategies

from odoo_client.client.client import OdooClient
from odoo_client.settings import OdooConnectionSettings, build_connection_settings
from odoo_client.records.recordset import OdooRecordset
from odoo_client.transport.errors import OdooAuthenticationError, OdooServerError
from odoo_client.transport.executor import OdooExecutor
from odoo_client.transport.rpc import OdooRpcExecutor


class TestOdooClientContract(unittest.TestCase):
    @given(strategies.text())
    def test_client_exposes_root_env_with_injected_executor(
        self, model_name: str
    ) -> None:
        executor = Mock(spec=OdooExecutor)
        client = OdooClient(executor=executor)

        first_recordset = client[model_name].with_context({"lang": "en_US"})
        model = client[model_name]

        self.assertIsInstance(model, OdooRecordset)
        self.assertEqual(model.executor, executor)
        self.assertEqual(model.context, {})
        self.assertIsNot(first_recordset, model)
        self.assertIs(client[model_name], model)
        self.assertEqual(model.model_name, model_name)
        self.assertEqual(model.ids, ())
        # Verify shared metadata cache
        self.assertIs(first_recordset.metadata_cache, model.metadata_cache)

    def test_client_and_env_recordsets_share_cached_metadata(self) -> None:
        executor = Mock(spec=OdooExecutor)
        executor.execute.return_value = {"name": {"type": "char"}}
        client = OdooClient(executor=executor)

        first = client["res.partner"].fields_get(["name"], ["type"])
        second = client["res.partner"].fields_get(["name"], ["type"])

        self.assertEqual(first, second)
        executor.execute.assert_called_once_with(
            "res.partner",
            "fields_get",
            allfields=["name"],
            attributes=["type"],
        )

    def test_client_is_not_a_mapping_or_iterable(self) -> None:
        executor = Mock(spec=OdooExecutor)
        client = OdooClient(executor=executor)

        self.assertNotIsInstance(client, Mapping)
        with self.assertRaises(TypeError):
            iter(client)

    @given(strategies.text())
    def test_client_uses_injected_executor_for_model_operations(
        self, model_name: str
    ) -> None:
        executor = Mock(spec=OdooExecutor)
        executor.execute.return_value = [{"id": 1, "name": "Test"}]

        client = OdooClient(executor=executor)

        result = client[model_name].browse([1]).read(["name"])

        self.assertEqual(result, [{"id": 1, "name": "Test"}])
        executor.execute.assert_called_once_with(
            model_name, "read", [1], fields=["name"]
        )

    def test_client_execute_delegates_to_executor(self) -> None:
        executor = Mock(spec=OdooExecutor)
        executor.execute.return_value = {"ok": True}
        client = OdooClient(executor=executor)

        result = client.execute("res.partner", "search", [("id", "=", 1)], limit=1)

        self.assertEqual(result, {"ok": True})
        executor.execute.assert_called_once_with(
            "res.partner", "search", [("id", "=", 1)], limit=1
        )

    def test_client_execute_propagates_sdk_error_without_wrapping(self) -> None:
        executor = Mock(spec=OdooExecutor)
        error = OdooServerError(
            "Odoo server error (res.partner.search)",
            operation="res.partner.search",
            model="res.partner",
            method="search",
        )
        executor.execute.side_effect = error
        client = OdooClient(executor=executor)

        with self.assertRaises(OdooServerError) as caught:
            client.execute("res.partner", "search", [])

        self.assertIs(caught.exception, error)

    @given(strategies.text())
    def test_client_reuses_cached_model_bound_recordset(self, model_name: str) -> None:
        executor = Mock(spec=OdooExecutor)
        client = OdooClient(executor=executor)

        first_model = client[model_name]
        second_model = client[model_name]

        self.assertIs(first_model, second_model)
        self.assertIsInstance(first_model, OdooRecordset)

    def test_client_uid_raises_for_non_rpc_executor(self) -> None:
        client = OdooClient(executor=Mock(spec=OdooExecutor))

        with self.assertRaises(AttributeError):
            _ = client.uid

    def test_client_uid_returns_rpc_executor_uid(self) -> None:
        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")
        executor._uid = 7
        client = OdooClient(executor=executor)

        self.assertEqual(client.uid, 7)

    @patch("odoo_client.transport.rpc.xmlrpc.client.ServerProxy")
    def test_client_authenticated_true_on_successful_login(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = 7
        mock_server_proxy.side_effect = [common_proxy, object_proxy]
        client = OdooClient(
            executor=OdooRpcExecutor("https://example.com", "db", "user", "pw")
        )

        self.assertTrue(client.authenticated)

    @patch("odoo_client.transport.rpc.xmlrpc.client.ServerProxy")
    def test_client_authenticated_false_on_failed_login(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = False
        mock_server_proxy.side_effect = [common_proxy, object_proxy]
        client = OdooClient(
            executor=OdooRpcExecutor("https://example.com", "db", "user", "pw")
        )

        self.assertFalse(client.authenticated)

    @patch("odoo_client.transport.rpc.xmlrpc.client.ServerProxy")
    def test_client_uid_raises_on_failed_login(self, mock_server_proxy: Mock) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = False
        mock_server_proxy.side_effect = [common_proxy, object_proxy]
        client = OdooClient(
            executor=OdooRpcExecutor("https://example.com", "db", "user", "pw")
        )

        with self.assertRaises(OdooAuthenticationError):
            _ = client.uid

    @patch("odoo_client.client.client.OdooRpcExecutor")
    def test_client_builds_rpc_executor_from_settings(
        self,
        mock_rpc_executor: Mock,
    ) -> None:
        settings = OdooConnectionSettings(
            url="https://example.com",
            db="example-db",
            username="example-user",
            password="example-password",
        )
        built_executor = Mock(spec=OdooExecutor)
        mock_rpc_executor.return_value = built_executor

        client = OdooClient.from_settings(settings)

        self.assertIs(client._executor, built_executor)
        mock_rpc_executor.assert_called_once_with(
            settings.url,
            settings.db,
            settings.username,
            settings.password,
            timeout=settings.timeout,
            credentials_refresh=None,
        )

    def test_client_requires_an_executor_or_settings(self) -> None:
        with self.assertRaisesRegex(ValueError, "executor or settings"):
            OdooClient()

    def test_injected_executor_wins_over_settings(self) -> None:
        executor = Mock(spec=OdooExecutor)
        settings = OdooConnectionSettings(
            url="https://example.com", db="d", username="u", password="p"
        )

        client = OdooClient(executor, settings=settings)

        self.assertIs(client._executor, executor)


_REQUIRED = {
    "url": "https://example.com",
    "db": "db",
    "username": "user",
    "password": "pw",
}


class TestBuildConnectionSettings(unittest.TestCase):
    def test_builds_settings_from_a_resolved_mapping(self) -> None:
        settings = build_connection_settings(_REQUIRED)

        self.assertEqual(settings.url, "https://example.com")
        self.assertEqual(settings.db, "db")
        self.assertEqual(settings.username, "user")
        self.assertEqual(settings.password, "pw")
        self.assertEqual(settings.transport, "xmlrpc")

    def test_raises_for_missing_configuration(self) -> None:
        with self.assertRaisesRegex(ValueError, "Missing Odoo connection settings"):
            build_connection_settings({})

    def test_api_key_and_transport_are_kept(self) -> None:
        settings = build_connection_settings(
            {
                "url": "https://example.com",
                "db": "mydb",
                "api_key": "secret-key",
                "transport": "json2",
            }
        )

        self.assertEqual(settings.api_key, "secret-key")
        self.assertEqual(settings.transport, "json2")

    def test_api_key_absent_from_repr(self) -> None:
        settings = OdooConnectionSettings(
            url="https://example.com",
            db="mydb",
            api_key="super-secret",
        )

        self.assertNotIn("super-secret", repr(settings))

    def test_json2_transport_raises_without_api_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "Missing Odoo connection settings"):
            build_connection_settings(
                {"url": "https://example.com", "db": "mydb", "transport": "json2"}
            )

    def test_xmlrpc_transport_raises_without_username_password(self) -> None:
        with self.assertRaisesRegex(ValueError, "Missing Odoo connection settings"):
            build_connection_settings({"url": "https://example.com", "db": "mydb"})


class TestConnectionSettingsTimeout(unittest.TestCase):
    def _timeout(self, value: object) -> float:
        return build_connection_settings({**_REQUIRED, "timeout": value}).timeout

    def test_timeout_defaults_to_thirty_seconds(self) -> None:
        self.assertEqual(build_connection_settings(_REQUIRED).timeout, 30.0)

    def test_numeric_string_timeout_parsed_as_float(self) -> None:
        self.assertEqual(self._timeout("12.5"), 12.5)

    def test_float_timeout_is_kept(self) -> None:
        self.assertEqual(self._timeout(45.0), 45.0)

    def test_non_numeric_timeout_falls_back_to_default(self) -> None:
        self.assertEqual(self._timeout("not-a-number"), 30.0)

    def test_zero_timeout_falls_back_to_default(self) -> None:
        self.assertEqual(self._timeout("0"), 30.0)

    def test_negative_timeout_falls_back_to_default(self) -> None:
        self.assertEqual(self._timeout("-3"), 30.0)

    def test_infinite_timeout_falls_back_to_default(self) -> None:
        self.assertEqual(self._timeout("inf"), 30.0)

    def test_nan_timeout_falls_back_to_default(self) -> None:
        self.assertEqual(self._timeout("nan"), 30.0)

    def test_boolean_timeout_falls_back_to_default(self) -> None:
        self.assertEqual(self._timeout(True), 30.0)


class TestOdooClientTimeoutThreading(unittest.TestCase):
    @patch("odoo_client.client.client.OdooRpcExecutor")
    def test_timeout_reaches_rpc_executor(self, mock_rpc: Mock) -> None:
        mock_rpc.return_value = Mock(spec=OdooExecutor)
        settings = build_connection_settings({**_REQUIRED, "timeout": "45.5"})

        OdooClient.from_settings(settings)

        mock_rpc.assert_called_once_with(
            "https://example.com",
            "db",
            "user",
            "pw",
            timeout=45.5,
            credentials_refresh=None,
        )

    @patch("odoo_client.client.client.OdooJson2Executor")
    def test_timeout_reaches_json2_executor(self, mock_json2: Mock) -> None:
        mock_json2.return_value = Mock(spec=OdooExecutor)
        settings = build_connection_settings(
            {
                "url": "https://example.com",
                "db": "db",
                "api_key": "key",
                "transport": "json2",
                "timeout": "7",
            }
        )

        OdooClient.from_settings(settings)

        mock_json2.assert_called_once_with(
            "https://example.com",
            "db",
            "key",
            timeout=7.0,
            credentials_refresh=None,
        )

    @patch("odoo_client.client.client.OdooRpcExecutor")
    def test_garbage_timeout_reaches_executor_as_default(self, mock_rpc: Mock) -> None:
        mock_rpc.return_value = Mock(spec=OdooExecutor)
        settings = build_connection_settings({**_REQUIRED, "timeout": "banana"})

        OdooClient.from_settings(settings)

        mock_rpc.assert_called_once_with(
            "https://example.com",
            "db",
            "user",
            "pw",
            timeout=30.0,
            credentials_refresh=None,
        )


class TestOdooClientFactoryMethods(unittest.TestCase):
    @patch("odoo_client.client.client.OdooRpcExecutor")
    def test_from_xml_rpc_returns_odoo_client(self, mock_rpc: Mock) -> None:
        mock_executor = Mock(spec=OdooExecutor)
        mock_rpc.return_value = mock_executor

        client = OdooClient.from_xml_rpc("https://example.com", "mydb", "admin", "pass")

        self.assertIsInstance(client, OdooClient)
        mock_rpc.assert_called_once_with("https://example.com", "mydb", "admin", "pass")
        self.assertIs(client._executor, mock_executor)

    @patch("odoo_client.client.client.OdooJson2Executor")
    def test_from_json2_returns_odoo_client(self, mock_json2: Mock) -> None:
        mock_executor = Mock(spec=OdooExecutor)
        mock_json2.return_value = mock_executor

        client = OdooClient.from_json2("https://example.com", "mydb", "api-key")

        self.assertIsInstance(client, OdooClient)
        mock_json2.assert_called_once_with("https://example.com", "mydb", "api-key")
        self.assertIs(client._executor, mock_executor)

    @patch("odoo_client.client.client.OdooJson2Executor")
    def test_from_settings_uses_json2_executor_when_transport_is_json2(
        self,
        mock_json2: Mock,
    ) -> None:
        settings = OdooConnectionSettings(
            url="https://example.com",
            db="mydb",
            transport="json2",
            api_key="my-api-key",
        )
        built_executor = Mock(spec=OdooExecutor)
        mock_json2.return_value = built_executor

        client = OdooClient.from_settings(settings)

        self.assertIs(client._executor, built_executor)
        mock_json2.assert_called_once_with(
            settings.url,
            settings.db,
            settings.api_key,
            timeout=settings.timeout,
            credentials_refresh=None,
        )


class TestOdooClientCredentialRefresh(unittest.TestCase):
    @patch("odoo_client.client.client.OdooRpcExecutor")
    def test_from_settings_passes_the_refresh_hook_to_the_executor(
        self, mock_rpc_executor: Mock
    ) -> None:
        settings = OdooConnectionSettings(
            url="https://example.com",
            db="example-db",
            username="example-user",
            password="example-password",
        )
        refresh = Mock(return_value=settings)
        mock_rpc_executor.return_value = Mock(spec=OdooExecutor)

        OdooClient.from_settings(settings, credentials_refresh=refresh)

        self.assertIs(
            mock_rpc_executor.call_args.kwargs["credentials_refresh"], refresh
        )

    @patch("odoo_client.client.client.OdooJson2Executor")
    def test_from_settings_passes_the_refresh_hook_to_the_json2_executor(
        self, mock_json2: Mock
    ) -> None:
        settings = OdooConnectionSettings(
            url="https://example.com", db="d", transport="json2", api_key="k"
        )
        refresh = Mock(return_value=settings)
        mock_json2.return_value = Mock(spec=OdooExecutor)

        OdooClient.from_settings(settings, credentials_refresh=refresh)

        self.assertIs(mock_json2.call_args.kwargs["credentials_refresh"], refresh)

    @patch("odoo_client.client.client.OdooRpcExecutor")
    def test_from_xml_rpc_passes_no_refresh_hook(self, mock_rpc: Mock) -> None:
        mock_rpc.return_value = Mock(spec=OdooExecutor)

        OdooClient.from_xml_rpc("https://example.com", "mydb", "admin", "pass")

        self.assertNotIn("credentials_refresh", mock_rpc.call_args.kwargs)

    @patch("odoo_client.client.client.OdooJson2Executor")
    def test_from_json2_passes_no_refresh_hook(self, mock_json2: Mock) -> None:
        mock_json2.return_value = Mock(spec=OdooExecutor)

        OdooClient.from_json2("https://example.com", "mydb", "api-key")

        self.assertNotIn("credentials_refresh", mock_json2.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
