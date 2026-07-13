"""Tests for LocalConfig resolution precedence (File > Env > Default)."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from odoo_sdk.state.config import (
    DEFAULT_LOCAL_CONFIG_PATH,
    LOCAL_CONFIG_ENV_VAR,
    LocalConfig,
    OdooConnectionSettings,
)


def _write(dirpath: str, name: str, content: str) -> str:
    path = Path(dirpath) / name
    path.write_text(content, encoding="utf-8")
    return str(path)


_TOML = (
    "[connection]\n"
    'url = "https://from-file.example.com"\n'
    'db = "file-db"\n'
    'api_key = "file-key"\n'
    'transport = "json2"\n'
    "\n"
    "[behavior]\n"
    'log_level = "debug"\n'
)


class TestLocalConfigDefaults(unittest.TestCase):
    def test_defaults_applied_when_no_file_and_no_env(self):
        with patch.dict("os.environ", {}, clear=True):
            config = LocalConfig.load(config_path=None)
        self.assertEqual(config.connection["transport"], "xmlrpc")
        self.assertIsNone(config.connection["url"])
        self.assertIsNone(config.connection["db"])

    def test_direct_construction_merges_defaults(self):
        config = LocalConfig(connection={"url": "https://x"}, behavior={"k": "v"})
        self.assertEqual(config.connection["url"], "https://x")
        self.assertEqual(config.connection["transport"], "xmlrpc")
        self.assertEqual(config.behavior["k"], "v")
        self.assertEqual(config.get("k"), "v")
        self.assertEqual(config.get("missing", "fallback"), "fallback")


class TestLocalConfigFilePrecedence(unittest.TestCase):
    def test_file_values_win_over_env_and_default(self):
        with TemporaryDirectory() as tmp:
            path = _write(tmp, "config.toml", _TOML)
            env = {
                "ODOO_URL": "https://from-env.example.com",
                "ODOO_DB": "env-db",
            }
            with patch.dict("os.environ", env, clear=True):
                config = LocalConfig.load(config_path=path)
        # File wins over both env and default.
        self.assertEqual(config.connection["url"], "https://from-file.example.com")
        self.assertEqual(config.connection["db"], "file-db")
        self.assertEqual(config.connection["transport"], "json2")
        self.assertEqual(config.behavior["log_level"], "debug")

    def test_reads_toml_config_file(self):
        with TemporaryDirectory() as tmp:
            path = _write(tmp, "config.toml", _TOML)
            with patch.dict("os.environ", {}, clear=True):
                config = LocalConfig.load(config_path=path)
        self.assertEqual(config.connection["api_key"], "file-key")

    def test_reads_ini_config_file(self):
        ini = (
            "[connection]\n"
            "url = https://ini.example.com\n"
            "db = ini-db\n"
            "username = ini-user\n"
            "password = ini-pass\n"
        )
        with TemporaryDirectory() as tmp:
            path = _write(tmp, "config.ini", ini)
            with patch.dict("os.environ", {}, clear=True):
                config = LocalConfig.load(config_path=path)
        self.assertEqual(config.connection["url"], "https://ini.example.com")
        self.assertEqual(config.connection["username"], "ini-user")


class TestLocalConfigEnvPrecedence(unittest.TestCase):
    def test_env_wins_over_default_when_file_absent(self):
        env = {
            "ODOO_URL": "https://from-env.example.com",
            "ODOO_DB": "env-db",
            "ODOO_TRANSPORT": "json2",
        }
        with patch.dict("os.environ", env, clear=True):
            # Point discovery at a nonexistent file so only env + default apply.
            config = LocalConfig.load(config_path="/nonexistent/config.toml")
        self.assertEqual(config.connection["url"], "https://from-env.example.com")
        self.assertEqual(config.connection["db"], "env-db")
        self.assertEqual(config.connection["transport"], "json2")

    def test_partial_file_falls_back_to_env_then_default(self):
        partial = "[connection]\nurl = \"https://file-only.example.com\"\n"
        with TemporaryDirectory() as tmp:
            path = _write(tmp, "config.toml", partial)
            env = {"ODOO_DB": "env-db"}
            with patch.dict("os.environ", env, clear=True):
                config = LocalConfig.load(config_path=path)
        self.assertEqual(config.connection["url"], "https://file-only.example.com")  # file
        self.assertEqual(config.connection["db"], "env-db")  # env
        self.assertEqual(config.connection["transport"], "xmlrpc")  # default


class TestLocalConfigDiscovery(unittest.TestCase):
    def test_env_var_overrides_config_path(self):
        with TemporaryDirectory() as tmp:
            path = _write(tmp, "config.toml", _TOML)
            with patch.dict(
                "os.environ", {LOCAL_CONFIG_ENV_VAR: path}, clear=True
            ):
                config = LocalConfig.load()
        self.assertEqual(config.connection["url"], "https://from-file.example.com")

    def test_default_path_used_when_no_override(self):
        self.assertEqual(DEFAULT_LOCAL_CONFIG_PATH, "~/.config/odoo-sdk/config.toml")
        with patch.dict("os.environ", {}, clear=True):
            with patch(
                "odoo_sdk.state.config.Path.is_file", return_value=False
            ):
                config = LocalConfig.load()
        self.assertEqual(config.connection["transport"], "xmlrpc")


class TestLocalConfigConnectionSettings(unittest.TestCase):
    def test_builds_validated_connection_settings(self):
        config = LocalConfig(
            connection={
                "url": "https://x.example.com",
                "db": "db",
                "api_key": "key",
                "transport": "json2",
            }
        )
        settings = config.connection_settings()
        self.assertIsInstance(settings, OdooConnectionSettings)
        self.assertEqual(settings.url, "https://x.example.com")
        self.assertEqual(settings.transport, "json2")
        self.assertEqual(settings.api_key, "key")

    def test_raises_when_required_settings_missing(self):
        config = LocalConfig(connection={})
        with patch.dict("os.environ", {}, clear=True):
            with patch(
                "odoo_sdk.state.config._resolve_config_path", return_value=None
            ):
                with self.assertRaisesRegex(
                    ValueError, "Missing Odoo connection settings"
                ):
                    config.connection_settings()


class TestOdooConnectionSettingsRepr(unittest.TestCase):
    """Guard against secrets leaking through the dataclass repr/str."""

    def _fully_populated(self) -> OdooConnectionSettings:
        return OdooConnectionSettings(
            url="https://odoo.example.com",
            db="prod-db",
            username="admin",
            password="s3cr3t-pw",
            transport="json2",
            api_key="k3y-abc",
        )

    def test_repr_matches_exact_expected_string(self):
        settings = self._fully_populated()
        expected = (
            "OdooConnectionSettings("
            "url='https://odoo.example.com', "
            "db='prod-db', "
            "username='admin', "
            "transport='json2')"
        )
        self.assertEqual(repr(settings), expected)

    def test_str_matches_exact_expected_string(self):
        settings = self._fully_populated()
        expected = (
            "OdooConnectionSettings("
            "url='https://odoo.example.com', "
            "db='prod-db', "
            "username='admin', "
            "transport='json2')"
        )
        self.assertEqual(str(settings), expected)

    def test_password_value_absent_from_repr_and_str(self):
        settings = self._fully_populated()
        self.assertNotIn("s3cr3t-pw", repr(settings))
        self.assertNotIn("s3cr3t-pw", str(settings))

    def test_api_key_value_absent_from_repr_and_str(self):
        settings = self._fully_populated()
        self.assertNotIn("k3y-abc", repr(settings))
        self.assertNotIn("k3y-abc", str(settings))


if __name__ == "__main__":
    unittest.main()
