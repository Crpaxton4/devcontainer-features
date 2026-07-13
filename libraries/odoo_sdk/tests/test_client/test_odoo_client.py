import unittest
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from hypothesis import given, strategies

from odoo_sdk.client.client import OdooClient
from odoo_sdk.state.config import OdooConnectionSettings
from odoo_sdk.records.recordset import OdooRecordset
from odoo_sdk.transport.errors import OdooAuthenticationError, OdooServerError
from odoo_sdk.transport.executor import OdooExecutor
from odoo_sdk.transport.json2 import OdooJson2Executor
from odoo_sdk.transport.rpc import OdooRpcExecutor


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

    @given(strategies.text(), strategies.text(), strategies.text(), strategies.text())
    def test_client_is_not_a_mapping_or_iterable(
        self, url: str, db: str, username: str, password: str
    ) -> None:
        executor = Mock(spec=OdooExecutor)
        client = OdooClient(url, db, username, password, executor=executor)

        self.assertNotIsInstance(client, Mapping)
        with self.assertRaises(TypeError):
            iter(client)

    @given(strategies.text())
    def test_client_uses_injected_executor_for_model_operations(
        self, model_name: str
    ) -> None:
        executor = Mock(spec=OdooExecutor)
        executor.execute.return_value = [{"id": 1, "name": "Test"}]

        client = OdooClient(
            "https://example.com",
            "db",
            "user",
            "pw",
            executor=executor,
        )

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

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
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

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
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

    @patch("odoo_sdk.transport.rpc.xmlrpc.client.ServerProxy")
    def test_client_uid_raises_on_failed_login(
        self, mock_server_proxy: Mock
    ) -> None:
        common_proxy = Mock()
        object_proxy = Mock()
        common_proxy.authenticate.return_value = False
        mock_server_proxy.side_effect = [common_proxy, object_proxy]
        client = OdooClient(
            executor=OdooRpcExecutor("https://example.com", "db", "user", "pw")
        )

        with self.assertRaises(OdooAuthenticationError):
            _ = client.uid

    @patch("odoo_sdk.client.client.OdooRpcExecutor")
    @patch("odoo_sdk.client.client.OdooConnectionSettings.from_sources")
    def test_client_builds_rpc_executor_from_resolved_settings(
        self,
        mock_from_sources: Mock,
        mock_rpc_executor: Mock,
    ) -> None:
        settings = OdooConnectionSettings(
            url="https://example.com",
            db="example-db",
            username="example-user",
            password="example-password",
        )
        built_executor = Mock(spec=OdooExecutor)
        mock_from_sources.return_value = settings
        mock_rpc_executor.return_value = built_executor

        client = OdooClient(
            url="https://ignored.example.com",
            db="ignored-db",
            username="ignored-user",
            password="ignored-password",
            config_path="ignored.ini",
        )

        self.assertIs(client._executor, built_executor)
        mock_from_sources.assert_called_once_with(
            url="https://ignored.example.com",
            db="ignored-db",
            username="ignored-user",
            password="ignored-password",
            config_path="ignored.ini",
        )
        mock_rpc_executor.assert_called_once_with(
            settings.url,
            settings.db,
            settings.username,
            settings.password,
        )

    def test_client_uses_ini_configuration_when_values_omitted(self) -> None:
        executor = Mock(spec=OdooExecutor)
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "odoo.ini"
            config_path.write_text(
                "[odoo]\n"
                "url=https://from-file.example.com\n"
                "db=file-db\n"
                "username=file-user\n"
                "password=file-password\n",
                encoding="utf-8",
            )

            client = OdooClient(executor=executor, config_path=str(config_path))

        self.assertIsInstance(client, OdooClient)


class TestOdooConnectionSettings(unittest.TestCase):
    def test_explicit_values_override_file_values_individually(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "odoo.ini"
            config_path.write_text(
                "[odoo]\n"
                "url=https://from-file.example.com\n"
                "db=file-db\n"
                "username=file-user\n"
                "password=file-password\n",
                encoding="utf-8",
            )

            settings = OdooConnectionSettings.from_sources(
                url="https://explicit.example.com",
                db="explicit-db",
                config_path=str(config_path),
            )

        self.assertEqual(settings.url, "https://explicit.example.com")
        self.assertEqual(settings.db, "explicit-db")
        self.assertEqual(settings.username, "file-user")
        self.assertEqual(settings.password, "file-password")

    def test_reads_ini_file_when_environment_is_not_set(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "odoo.ini"
            config_path.write_text(
                "[odoo]\n"
                "url=https://from-file.example.com\n"
                "db=file-db\n"
                "username=file-user\n"
                "password=file-password\n",
                encoding="utf-8",
            )

            settings = OdooConnectionSettings.from_sources(config_path=str(config_path))

        self.assertEqual(settings.url, "https://from-file.example.com")
        self.assertEqual(settings.db, "file-db")
        self.assertEqual(settings.username, "file-user")
        self.assertEqual(settings.password, "file-password")

    def test_raises_for_missing_configuration(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with patch(
                "odoo_sdk.state.config._resolve_config_path",
                return_value=None,
            ):
                with self.assertRaisesRegex(
                    ValueError, "Missing Odoo connection settings"
                ):
                    OdooConnectionSettings.from_sources()

    def test_uses_config_path_from_environment_when_present(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "odoo.ini"
            config_path.write_text(
                "[odoo]\n"
                "url=https://from-file.example.com\n"
                "db=file-db\n"
                "username=file-user\n"
                "password=file-password\n",
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"odoo_sdk_CONFIG": str(config_path)}):
                settings = OdooConnectionSettings.from_sources()

        self.assertEqual(settings.url, "https://from-file.example.com")
        self.assertEqual(settings.db, "file-db")
        self.assertEqual(settings.username, "file-user")
        self.assertEqual(settings.password, "file-password")

    def test_reads_connection_values_from_environment_when_present(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "ODOO_URL": "https://from-environment.example.com",
                "ODOO_DB": "environment-db",
                "ODOO_USERNAME": "environment-user",
                "ODOO_PASSWORD": "environment-password",
            },
            clear=True,
        ):
            settings = OdooConnectionSettings.from_sources()

        self.assertEqual(settings.url, "https://from-environment.example.com")
        self.assertEqual(settings.db, "environment-db")
        self.assertEqual(settings.username, "environment-user")
        self.assertEqual(settings.password, "environment-password")

    def test_environment_values_override_file_values(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "odoo.ini"
            config_path.write_text(
                "[odoo]\n"
                "url=https://from-file.example.com\n"
                "db=file-db\n"
                "username=file-user\n"
                "password=file-password\n",
                encoding="utf-8",
            )

            with patch.dict(
                "os.environ",
                {
                    "ODOO_URL": "https://from-environment.example.com",
                    "ODOO_PASSWORD": "environment-password",
                },
                clear=True,
            ):
                settings = OdooConnectionSettings.from_sources(
                    config_path=str(config_path)
                )

        self.assertEqual(settings.url, "https://from-environment.example.com")
        self.assertEqual(settings.db, "file-db")
        self.assertEqual(settings.username, "file-user")
        self.assertEqual(settings.password, "environment-password")

    @patch("odoo_sdk.state.config._resolve_relative_to_invoking_script")
    def test_relative_config_path_resolves_from_invoking_script_directory(
        self, mock_resolve_relative_to_invoking_script: Mock
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "odoo.ini"
            config_path.write_text(
                "[odoo]\n"
                "url=https://from-file.example.com\n"
                "db=file-db\n"
                "username=file-user\n"
                "password=file-password\n",
                encoding="utf-8",
            )

            mock_resolve_relative_to_invoking_script.return_value = str(config_path)
            settings = OdooConnectionSettings.from_sources(config_path="odoo.ini")

        self.assertEqual(settings.url, "https://from-file.example.com")

    def test_api_key_resolved_from_environment_variable(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "ODOO_URL": "https://example.com",
                "ODOO_DB": "mydb",
                "ODOO_API_KEY": "secret-key",
                "ODOO_TRANSPORT": "json2",
            },
            clear=True,
        ):
            settings = OdooConnectionSettings.from_sources()

        self.assertEqual(settings.api_key, "secret-key")

    def test_transport_resolved_from_environment_variable(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "ODOO_URL": "https://example.com",
                "ODOO_DB": "mydb",
                "ODOO_API_KEY": "secret-key",
                "ODOO_TRANSPORT": "json2",
            },
            clear=True,
        ):
            settings = OdooConnectionSettings.from_sources()

        self.assertEqual(settings.transport, "json2")

    def test_api_key_absent_from_repr(self) -> None:
        settings = OdooConnectionSettings(
            url="https://example.com",
            db="mydb",
            api_key="super-secret",
        )

        self.assertNotIn("super-secret", repr(settings))

    def test_ini_file_api_key_and_transport_parsed(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "odoo.ini"
            config_path.write_text(
                "[odoo]\n"
                "url=https://from-file.example.com\n"
                "db=file-db\n"
                "transport=json2\n"
                "api_key=ini-api-key\n",
                encoding="utf-8",
            )

            settings = OdooConnectionSettings.from_sources(config_path=str(config_path))

        self.assertEqual(settings.transport, "json2")
        self.assertEqual(settings.api_key, "ini-api-key")

    def test_json2_transport_raises_without_api_key(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "ODOO_URL": "https://example.com",
                "ODOO_DB": "mydb",
                "ODOO_TRANSPORT": "json2",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "Missing Odoo connection settings"):
                OdooConnectionSettings.from_sources()

    def test_xmlrpc_transport_raises_without_username_password(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "ODOO_URL": "https://example.com",
                "ODOO_DB": "mydb",
            },
            clear=True,
        ):
            with patch(
                "odoo_sdk.state.config._resolve_config_path",
                return_value=None,
            ):
                with self.assertRaisesRegex(
                    ValueError, "Missing Odoo connection settings"
                ):
                    OdooConnectionSettings.from_sources()


class TestOdooClientFactoryMethods(unittest.TestCase):
    @patch("odoo_sdk.client.client.OdooRpcExecutor")
    def test_from_xml_rpc_returns_odoo_client(self, mock_rpc: Mock) -> None:
        mock_executor = Mock(spec=OdooExecutor)
        mock_rpc.return_value = mock_executor

        client = OdooClient.from_xml_rpc("https://example.com", "mydb", "admin", "pass")

        self.assertIsInstance(client, OdooClient)
        mock_rpc.assert_called_once_with("https://example.com", "mydb", "admin", "pass")
        self.assertIs(client._executor, mock_executor)

    @patch("odoo_sdk.client.client.OdooJson2Executor")
    def test_from_json2_returns_odoo_client(self, mock_json2: Mock) -> None:
        mock_executor = Mock(spec=OdooExecutor)
        mock_json2.return_value = mock_executor

        client = OdooClient.from_json2("https://example.com", "mydb", "api-key")

        self.assertIsInstance(client, OdooClient)
        mock_json2.assert_called_once_with("https://example.com", "mydb", "api-key")
        self.assertIs(client._executor, mock_executor)

    @patch("odoo_sdk.client.client.OdooJson2Executor")
    @patch("odoo_sdk.client.client.OdooConnectionSettings.from_sources")
    def test_client_init_uses_json2_executor_when_transport_is_json2(
        self,
        mock_from_sources: Mock,
        mock_json2: Mock,
    ) -> None:
        settings = OdooConnectionSettings(
            url="https://example.com",
            db="mydb",
            transport="json2",
            api_key="my-api-key",
        )
        built_executor = Mock(spec=OdooExecutor)
        mock_from_sources.return_value = settings
        mock_json2.return_value = built_executor

        client = OdooClient(url="https://example.com", db="mydb")

        self.assertIs(client._executor, built_executor)
        mock_json2.assert_called_once_with(
            settings.url,
            settings.db,
            settings.api_key,
        )


class TestOdooClientFromConfig(unittest.TestCase):
    @patch("odoo_sdk.client.client.OdooRpcExecutor")
    def test_from_config_builds_executor_from_local_config(
        self, mock_rpc: Mock
    ) -> None:
        from odoo_sdk.state.config import LocalConfig

        built = Mock(spec=OdooExecutor)
        mock_rpc.return_value = built
        config = LocalConfig(
            connection={
                "url": "https://cfg.example.com",
                "db": "cfg-db",
                "username": "cfg-user",
                "password": "cfg-pass",
            }
        )

        client = OdooClient.from_config(config)

        self.assertIs(client._executor, built)
        mock_rpc.assert_called_once_with(
            "https://cfg.example.com", "cfg-db", "cfg-user", "cfg-pass"
        )

    @patch("odoo_sdk.client.client.OdooJson2Executor")
    def test_config_kwarg_selects_json2_transport(self, mock_json2: Mock) -> None:
        from odoo_sdk.state.config import LocalConfig

        built = Mock(spec=OdooExecutor)
        mock_json2.return_value = built
        config = LocalConfig(
            connection={
                "url": "https://cfg.example.com",
                "db": "cfg-db",
                "api_key": "cfg-key",
                "transport": "json2",
            }
        )

        client = OdooClient(config=config)

        self.assertIs(client._executor, built)
        mock_json2.assert_called_once_with(
            "https://cfg.example.com", "cfg-db", "cfg-key"
        )


if __name__ == "__main__":
    unittest.main()
