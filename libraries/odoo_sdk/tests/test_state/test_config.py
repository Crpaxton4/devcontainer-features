"""Tests for LocalConfig resolution precedence (File > Env > Default)."""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from odoo_sdk.state.config import (
    DEFAULT_CONFIG_DIR,
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
        # Force discovery to find no file so the test is independent of any real
        # config on the host (e.g. the owner's ~/.config/odoo_sdk/config.ini).
        with patch.dict("os.environ", {}, clear=True):
            with patch("odoo_sdk.state.config.Path.is_file", return_value=False):
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

    def test_default_dir_uses_underscore_form_matching_the_mount(self):
        # The default discovery directory must be the underscore form that
        # matches the feature's bind mount (~/.config/odoo_sdk), not the old
        # hyphenated ~/.config/odoo-sdk.
        self.assertEqual(DEFAULT_CONFIG_DIR, "~/.config/odoo_sdk")

    def test_default_path_used_when_no_override(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch(
                "odoo_sdk.state.config.Path.is_file", return_value=False
            ):
                config = LocalConfig.load()
        self.assertEqual(config.connection["transport"], "xmlrpc")

    def test_env_var_directory_probed_for_config_file(self):
        # ODOO_SDK_CONFIG may point at a DIRECTORY; discovery probes it for
        # config.toml then config.ini so the feature can point the var at the
        # mounted config dir regardless of which file the user created.
        with TemporaryDirectory() as tmp:
            _write(tmp, "config.toml", _TOML)
            with patch.dict("os.environ", {LOCAL_CONFIG_ENV_VAR: tmp}, clear=True):
                config = LocalConfig.load()
        self.assertEqual(config.connection["url"], "https://from-file.example.com")

    def test_config_toml_wins_over_config_ini_in_probed_directory(self):
        # When a directory holds both files, config.toml is probed first.
        ini = "[connection]\nurl = https://from-ini.example.com\ndb = ini-db\n"
        with TemporaryDirectory() as tmp:
            _write(tmp, "config.toml", _TOML)
            _write(tmp, "config.ini", ini)
            with patch.dict("os.environ", {LOCAL_CONFIG_ENV_VAR: tmp}, clear=True):
                config = LocalConfig.load()
        self.assertEqual(config.connection["url"], "https://from-file.example.com")

    def test_cwd_config_file_discovered_when_no_override(self):
        # ./.odoo_sdk.toml in the current working directory is discovered ahead
        # of the ~/.config/odoo_sdk default.
        with TemporaryDirectory() as tmp:
            _write(tmp, ".odoo_sdk.toml", _TOML)
            original = os.getcwd()
            os.chdir(tmp)
            try:
                with patch.dict("os.environ", {}, clear=True):
                    config = LocalConfig.load()
            finally:
                os.chdir(original)
        self.assertEqual(config.connection["url"], "https://from-file.example.com")

    def test_legacy_odoo_section_ini_in_directory_still_resolves(self):
        # Compatibility guard: an already-persisted config.ini using the legacy
        # [odoo] section, with ODOO_SDK_CONFIG pointing at its DIRECTORY, keeps
        # authenticating unchanged.
        ini = (
            "[odoo]\n"
            "url = https://persisted.example.com\n"
            "db = persisted-db\n"
            "username = persisted-user\n"
            "password = persisted-pass\n"
        )
        with TemporaryDirectory() as tmp:
            _write(tmp, "config.ini", ini)
            with patch.dict("os.environ", {LOCAL_CONFIG_ENV_VAR: tmp}, clear=True):
                config = LocalConfig.load()
                settings = config.connection_settings()
        self.assertEqual(settings.url, "https://persisted.example.com")
        self.assertEqual(settings.db, "persisted-db")
        self.assertEqual(settings.username, "persisted-user")
        self.assertEqual(settings.password, "persisted-pass")

    def test_explicit_connection_section_wins_over_odoo_alias(self):
        # When both [connection] and the [odoo] alias are present, the canonical
        # [connection] section is used.
        ini = (
            "[connection]\n"
            "url = https://canonical.example.com\n"
            "db = canonical-db\n"
            "[odoo]\n"
            "url = https://alias.example.com\n"
            "db = alias-db\n"
        )
        with TemporaryDirectory() as tmp:
            path = _write(tmp, "config.ini", ini)
            with patch.dict("os.environ", {}, clear=True):
                config = LocalConfig.load(config_path=path)
        self.assertEqual(config.connection["url"], "https://canonical.example.com")
        self.assertEqual(config.connection["db"], "canonical-db")


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
        # An empty connection mapping resolves to all-default (all None) values,
        # which fail validation. connection_settings validates the already
        # resolved mapping directly, so no file/env access is involved.
        config = LocalConfig(connection={})
        with self.assertRaisesRegex(ValueError, "Missing Odoo connection settings"):
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
            "transport='json2', "
            "timeout=30.0)"
        )
        self.assertEqual(repr(settings), expected)

    def test_str_matches_exact_expected_string(self):
        settings = self._fully_populated()
        expected = (
            "OdooConnectionSettings("
            "url='https://odoo.example.com', "
            "db='prod-db', "
            "username='admin', "
            "transport='json2', "
            "timeout=30.0)"
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


class TestLocalConfigTimeout(unittest.TestCase):
    def test_timeout_absent_by_default(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("odoo_sdk.state.config.Path.is_file", return_value=False):
                config = LocalConfig.load(config_path=None)
        self.assertIsNone(config.connection["timeout"])

    def test_env_timeout_resolved_into_connection_mapping(self):
        with patch.dict("os.environ", {"ODOO_TIMEOUT": "15"}, clear=True):
            config = LocalConfig.load(config_path="/nonexistent/config.toml")
        self.assertEqual(config.connection["timeout"], "15")

    def test_file_timeout_wins_over_env(self):
        toml = (
            "[connection]\n"
            'url = "https://f.example.com"\n'
            'db = "file-db"\n'
            "timeout = 20\n"
        )
        with TemporaryDirectory() as tmp:
            path = _write(tmp, "config.toml", toml)
            with patch.dict("os.environ", {"ODOO_TIMEOUT": "8"}, clear=True):
                config = LocalConfig.load(config_path=path)
        self.assertEqual(config.connection["timeout"], 20)

    def test_connection_settings_coerces_string_timeout_to_float(self):
        config = LocalConfig(
            connection={
                "url": "https://x.example.com",
                "db": "db",
                "username": "user",
                "password": "pw",
                "timeout": "18",
            }
        )
        with patch.dict("os.environ", {}, clear=True):
            settings = config.connection_settings()
        self.assertEqual(settings.timeout, 18.0)

    def test_connection_settings_defaults_timeout_when_absent(self):
        config = LocalConfig(
            connection={
                "url": "https://x.example.com",
                "db": "db",
                "username": "user",
                "password": "pw",
            }
        )
        with patch.dict("os.environ", {}, clear=True):
            settings = config.connection_settings()
        self.assertEqual(settings.timeout, 30.0)

    def test_boolean_file_timeout_falls_back_to_default(self):
        toml = (
            "[connection]\n"
            'url = "https://f.example.com"\n'
            'db = "file-db"\n'
            'username = "file-user"\n'
            'password = "file-pass"\n'
            "timeout = true\n"
        )
        with TemporaryDirectory() as tmp:
            path = _write(tmp, "config.toml", toml)
            with patch.dict("os.environ", {}, clear=True):
                config = LocalConfig.load(config_path=path)
                settings = config.connection_settings()
        self.assertIs(config.connection["timeout"], True)
        self.assertEqual(settings.timeout, 30.0)


if __name__ == "__main__":
    unittest.main()
