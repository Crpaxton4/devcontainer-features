"""Tests for the ``[model_ids]`` map on LocalConfig (issue #686).

The map exists so no code path has to read ``ir.model`` to turn a model name into
the id a ``res_model_id`` / ``model_id`` field wants — that administrative table
must never be granted to a least-privileged service account (#444). These tests
pin the loader's two known spelling gotchas (TOML dotted keys, INI key casing),
the File > Environment Variable > Default precedence, load-time rejection of bad
values, and the actionable error raised for an unmapped model.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from odoo_sdk.state import LocalConfig
from odoo_sdk.state.config import MODEL_IDS_ENV_VAR


def _write(dirpath: str, name: str, content: str) -> str:
    path = Path(dirpath) / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def _load(content: str, name: str = "config.toml", env: dict = None) -> LocalConfig:
    """Load a config from a temp file with a fully controlled environment."""
    with TemporaryDirectory() as tmp:
        path = _write(tmp, name, content)
        with patch.dict("os.environ", env or {}, clear=True):
            return LocalConfig.load(config_path=path)


class TestModelIdsDefaults(unittest.TestCase):
    def test_default_is_empty_map(self):
        self.assertEqual(LocalConfig().model_ids, {})

    def test_absent_file_and_env_yields_empty_map(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("odoo_sdk.state.config.Path.is_file", return_value=False):
                config = LocalConfig.load(config_path=None)
        self.assertEqual(config.model_ids, {})

    def test_direct_construction_coerces_values(self):
        config = LocalConfig(model_ids={"project.task": "123"})
        self.assertEqual(config.model_ids, {"project.task": 123})

    def test_returned_map_is_a_copy(self):
        config = LocalConfig(model_ids={"project.task": 1})
        config.model_ids["res.partner"] = 2
        self.assertEqual(config.model_ids, {"project.task": 1})


class TestModelIdsTomlSpellings(unittest.TestCase):
    """TOML dotted keys under ``[model_ids]`` parse as nested tables, not flat keys."""

    def test_quoted_key(self):
        config = _load('[model_ids]\n"project.task" = 123\n')
        self.assertEqual(config.model_ids, {"project.task": 123})

    def test_unquoted_dotted_key_is_flattened(self):
        # Without flattening this parses as {"project": {"task": 123}}.
        config = _load("[model_ids]\nproject.task = 123\n")
        self.assertEqual(config.model_ids, {"project.task": 123})

    def test_explicit_subtable_is_flattened(self):
        config = _load("[model_ids.account]\nmove = 9\n")
        self.assertEqual(config.model_ids, {"account.move": 9})

    def test_mixed_spellings_coexist(self):
        config = _load(
            '[model_ids]\n"project.task" = 123\nres.partner = 77\n'
            "\n[model_ids.account]\nmove = 9\n"
        )
        self.assertEqual(
            config.model_ids,
            {"project.task": 123, "res.partner": 77, "account.move": 9},
        )


class TestModelIdsIni(unittest.TestCase):
    def test_ini_section_is_read(self):
        config = _load(
            "[connection]\nurl = https://x\n\n[model_ids]\nproject.task = 55\n",
            name="config.ini",
        )
        self.assertEqual(config.model_ids, {"project.task": 55})

    def test_ini_keys_are_lowercased_by_configparser(self):
        # Deliberate: ``optionxform`` lowercases option names and Odoo model names
        # are lowercase anyway, so the default is left in place.
        config = _load("[model_ids]\nProject.Task = 55\n", name="config.ini")
        self.assertEqual(config.model_ids, {"project.task": 55})

    def test_default_section_keys_do_not_leak_in(self):
        # ``parser.items(section)`` folds in ``[DEFAULT]``; an unrelated default
        # would otherwise fail model-id coercion and break the whole load.
        config = _load(
            "[DEFAULT]\nurl = https://x\n\n[model_ids]\nproject.task = 55\n",
            name="config.ini",
        )
        self.assertEqual(config.model_ids, {"project.task": 55})


class TestModelIdsPrecedence(unittest.TestCase):
    def test_env_supplies_the_map_when_the_file_has_no_section(self):
        config = _load(
            "[connection]\nurl = 'https://x'\n",
            env={MODEL_IDS_ENV_VAR: "project.task:123,res.partner:77"},
        )
        self.assertEqual(config.model_ids, {"project.task": 123, "res.partner": 77})

    def test_env_accepts_whitespace_separated_pairs(self):
        config = _load(
            "[connection]\nurl = 'https://x'\n",
            env={MODEL_IDS_ENV_VAR: "project.task:123 res.partner:77"},
        )
        self.assertEqual(config.model_ids, {"project.task": 123, "res.partner": 77})

    def test_file_wins_per_model_and_sources_union(self):
        config = _load(
            '[model_ids]\n"project.task" = 55\n',
            env={MODEL_IDS_ENV_VAR: "project.task:999,res.users:7"},
        )
        self.assertEqual(config.model_ids, {"project.task": 55, "res.users": 7})

    def test_whitespace_only_env_yields_an_empty_map(self):
        config = _load(
            "[connection]\nurl = 'https://x'\n",
            env={MODEL_IDS_ENV_VAR: "   "},
        )
        self.assertEqual(config.model_ids, {})

    def test_empty_file_value_falls_through_to_env(self):
        config = _load(
            "[model_ids]\nproject.task =\n",
            name="config.ini",
            env={MODEL_IDS_ENV_VAR: "project.task:999"},
        )
        self.assertEqual(config.model_ids, {"project.task": 999})


class TestModelIdsRejectsBadValues(unittest.TestCase):
    """A bad id raises at load rather than surfacing as an opaque XML-RPC fault."""

    def test_non_integer_value(self):
        with self.assertRaises(ValueError) as caught:
            _load('[model_ids]\n"project.task" = "abc"\n')
        self.assertIn("project.task", str(caught.exception))
        self.assertIn("positive integer", str(caught.exception))

    def test_zero_and_negative_values(self):
        for raw in ("0", "-3"):
            with self.subTest(value=raw):
                with self.assertRaises(ValueError):
                    _load(f'[model_ids]\n"project.task" = {raw}\n')

    def test_boolean_value_is_rejected_not_read_as_one(self):
        with self.assertRaises(ValueError):
            _load('[model_ids]\n"project.task" = true\n')

    def test_float_value_is_rejected(self):
        with self.assertRaises(ValueError):
            _load('[model_ids]\n"project.task" = 1.5\n')

    def test_malformed_env_pair_raises(self):
        with self.assertRaises(ValueError) as caught:
            _load(
                "[connection]\nurl = 'https://x'\n",
                env={MODEL_IDS_ENV_VAR: "project.task"},
            )
        self.assertIn(MODEL_IDS_ENV_VAR, str(caught.exception))

    def test_env_pair_with_empty_model_raises(self):
        with self.assertRaises(ValueError):
            _load(
                "[connection]\nurl = 'https://x'\n",
                env={MODEL_IDS_ENV_VAR: ":123"},
            )

    def test_direct_construction_rejects_bad_value(self):
        with self.assertRaises(ValueError):
            LocalConfig(model_ids={"project.task": "abc"})


class TestModelIdLookup(unittest.TestCase):
    def setUp(self):
        self.config = LocalConfig(model_ids={"project.task": 123})

    def test_model_id_returns_the_configured_id(self):
        self.assertEqual(self.config.model_id("project.task"), 123)

    def test_model_id_returns_none_when_absent(self):
        self.assertIsNone(self.config.model_id("mail.activity"))

    def test_require_model_id_returns_the_configured_id(self):
        self.assertEqual(self.config.require_model_id("project.task"), 123)

    def test_require_model_id_names_the_model_and_the_config_entry(self):
        with self.assertRaises(ValueError) as caught:
            self.config.require_model_id("mail.activity")
        message = str(caught.exception)
        # The message is the only discovery mechanism a user without server
        # access gets, so it must name the model and every way to supply the id.
        self.assertIn("mail.activity", message)
        self.assertIn("[model_ids]", message)
        self.assertIn(MODEL_IDS_ENV_VAR, message)

    def test_require_model_id_never_suggests_reading_ir_model(self):
        with self.assertRaises(ValueError) as caught:
            self.config.require_model_id("mail.activity")
        self.assertIn("never reads ir.model", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
