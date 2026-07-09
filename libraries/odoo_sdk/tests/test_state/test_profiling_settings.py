"""Tests for LocalConfig.profiling resolution (File > Env > Default)."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from odoo_sdk.state.config import LocalConfig


def _write(dirpath: str, name: str, content: str) -> str:
    path = Path(dirpath) / name
    path.write_text(content, encoding="utf-8")
    return str(path)


class TestProfilingDefault(unittest.TestCase):
    def test_disabled_by_default(self):
        with patch.dict("os.environ", {}, clear=True):
            config = LocalConfig.load(config_path=None)
        self.assertFalse(config.profiling)

    def test_direct_construction_defaults_false(self):
        self.assertFalse(LocalConfig().profiling)


class TestProfilingEnv(unittest.TestCase):
    def test_truthy_env_values_enable(self):
        for token in ("1", "true", "True", "YES", "on"):
            with patch.dict("os.environ", {"ODOO_PROFILING": token}, clear=True):
                self.assertTrue(
                    LocalConfig.load(config_path=None).profiling, msg=token
                )

    def test_falsy_env_values_disable(self):
        for token in ("0", "false", "no", "off", ""):
            with patch.dict("os.environ", {"ODOO_PROFILING": token}, clear=True):
                self.assertFalse(
                    LocalConfig.load(config_path=None).profiling, msg=token
                )


class TestProfilingFile(unittest.TestCase):
    def test_toml_behavior_true_enables(self):
        with TemporaryDirectory() as tmp:
            path = _write(tmp, "config.toml", "[behavior]\nprofiling = true\n")
            with patch.dict("os.environ", {}, clear=True):
                config = LocalConfig.load(config_path=path)
        self.assertTrue(config.profiling)

    def test_toml_behavior_false_disables(self):
        with TemporaryDirectory() as tmp:
            path = _write(tmp, "config.toml", "[behavior]\nprofiling = false\n")
            with patch.dict("os.environ", {}, clear=True):
                config = LocalConfig.load(config_path=path)
        self.assertFalse(config.profiling)

    def test_ini_behavior_on_enables(self):
        with TemporaryDirectory() as tmp:
            path = _write(tmp, "config.ini", "[behavior]\nprofiling = on\n")
            with patch.dict("os.environ", {}, clear=True):
                config = LocalConfig.load(config_path=path)
        self.assertTrue(config.profiling)

    def test_file_wins_over_env(self):
        # File says off, environment says on -> File takes precedence.
        with TemporaryDirectory() as tmp:
            path = _write(tmp, "config.toml", "[behavior]\nprofiling = false\n")
            with patch.dict("os.environ", {"ODOO_PROFILING": "1"}, clear=True):
                config = LocalConfig.load(config_path=path)
        self.assertFalse(config.profiling)


class TestProfilingDirectConstruction(unittest.TestCase):
    def test_bool_true(self):
        self.assertTrue(LocalConfig(behavior={"profiling": True}).profiling)

    def test_string_on(self):
        self.assertTrue(LocalConfig(behavior={"profiling": "on"}).profiling)

    def test_string_off(self):
        self.assertFalse(LocalConfig(behavior={"profiling": "off"}).profiling)


if __name__ == "__main__":
    unittest.main()
