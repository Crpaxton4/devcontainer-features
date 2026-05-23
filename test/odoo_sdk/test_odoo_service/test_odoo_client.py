import unittest
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from hypothesis import given, strategies

from odoo_sdk.odoo_service.odoo_client import OdooClient
from odoo_sdk.odoo_service.odoo_config import OdooConnectionSettings
from odoo_sdk.odoo_service.odoo_env import OdooEnv
from odoo_sdk.odoo_service.odoo_executor import OdooExecutor
from odoo_sdk.odoo_service.odoo_model import OdooModel
from odoo_sdk.odoo_service.odoo_query import OdooQuery
from odoo_sdk.odoo_service.odoo_rpc_executor import OdooRpcExecutor


class TestOdooClientContract(unittest.TestCase):
    @given(strategies.text())
    def test_client_exposes_root_env_with_injected_executor(
        self, model_name: str
    ) -> None:
        executor = Mock(spec=OdooExecutor)
        client = OdooClient(executor=executor)

        first_env = client.env.with_context({"lang": "en_US"})
        env = client.env
        model = env[model_name]

        self.assertIsInstance(env, OdooEnv)
        self.assertIs(env.executor, executor)
        self.assertEqual(env.context, {})
        self.assertIsNot(first_env, env)
        self.assertIs(client.env, env)
        self.assertEqual(model.name, model_name)
        self.assertIs(model.client, executor)

    def test_client_and_env_models_share_cached_metadata(self) -> None:
        executor = Mock(spec=OdooExecutor)
        executor.execute.return_value = {"name": {"type": "char"}}
        client = OdooClient(executor=executor)

        first = client["res.partner"].fields_get(["name"], ["type"])
        second = client.env["res.partner"].fields_get(["name"], ["type"])

        self.assertEqual(first, second)
        executor.execute.assert_called_once_with(
            "res.partner",
            "fields_get",
            allfields=["name"],
            attributes=["type"],
        )

    def test_direct_model_construction_reuses_client_root_env_cache(self) -> None:
        executor = Mock(spec=OdooExecutor)
        executor.execute.return_value = {"name": {"type": "char"}}
        client = OdooClient(executor=executor)
        model = OdooModel(client, "res.partner")

        first = client["res.partner"].fields_get(["name"], ["type"])
        second = model.fields_get(["name"], ["type"])

        self.assertEqual(first, second)
        executor.execute.assert_called_once_with(
            "res.partner",
            "fields_get",
            allfields=["name"],
            attributes=["type"],
        )

    def test_direct_query_construction_reuses_client_root_env(self) -> None:
        executor = Mock(spec=OdooExecutor)
        client = OdooClient(executor=executor)

        query = OdooQuery(client, "res.partner")

        self.assertIs(query._env, client.env)

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

        result = client[model_name].read([1], ["name"])

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

    @given(strategies.text())
    def test_client_reuses_cached_model_proxy(self, model_name: str) -> None:
        executor = Mock(spec=OdooExecutor)
        client = OdooClient(executor=executor)

        first_model = client[model_name]
        second_model = client[model_name]

        self.assertIs(first_model, second_model)

    def test_client_uid_raises_for_non_rpc_executor(self) -> None:
        client = OdooClient(executor=Mock(spec=OdooExecutor))

        with self.assertRaisesRegex(AttributeError, "does not expose uid"):
            _ = client.uid

    def test_client_uid_returns_rpc_executor_uid(self) -> None:
        executor = OdooRpcExecutor("https://example.com", "db", "user", "pw")
        executor._uid = 7
        executor._authenticated = True
        client = OdooClient(executor=executor)

        self.assertEqual(client.uid, 7)

    @patch("odoo_sdk.odoo_service.odoo_client.OdooRpcExecutor")
    @patch("odoo_sdk.odoo_service.odoo_client.OdooConnectionSettings.from_sources")
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
                "odoo_sdk.odoo_service.odoo_config._resolve_config_path",
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

    @patch("odoo_sdk.odoo_service.odoo_config._resolve_relative_to_invoking_script")
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

