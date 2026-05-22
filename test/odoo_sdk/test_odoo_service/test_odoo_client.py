import unittest
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from hypothesis import given, strategies

from odoo_sdk.odoo_service.odoo_client import OdooClient
from odoo_sdk.odoo_service.odoo_config import OdooConnectionSettings
from odoo_sdk.odoo_service.odoo_executor import OdooExecutor


class TestOdooClientContract(unittest.TestCase):
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
        with self.assertRaisesRegex(ValueError, "Missing Odoo connection settings"):
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
