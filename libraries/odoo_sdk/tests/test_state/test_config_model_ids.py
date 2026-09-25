"""Tests for the ``[model_ids]`` map on LocalConfig (issue #686).

The map exists so no code path has to read ``ir.model`` to turn a model name into
the id a ``res_model_id`` / ``model_id`` field wants — that administrative table
must never be granted to a least-privileged service account (#444). These tests
pin the loader's two known spelling gotchas (TOML dotted keys, INI key casing),
the File > Environment Variable > Default precedence, load-time rejection of bad
values, and the actionable error raised for an unmapped model.

Since #890 the map also has a writer (:meth:`LocalConfig.set_model_id`), so that
``get_models`` can fill an entry in once instead of an operator typing it. The
writer's tests are the second half of this file: they pin that it edits one key
of one section in place — every other section, key, comment and spelling
surviving — in both file shapes the loader accepts, and that an unwritable
destination is a reportable error rather than a crash.
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from odoo_sdk.state import LocalConfig, ModelIdsNotWritableError
from odoo_sdk.state.config import MODEL_IDS_ENV_VAR, persist_model_id_command


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

    def test_the_message_names_the_command_that_fills_the_entry_in(self):
        # #890: the message used to describe the gap without naming any way to
        # close it but hand-editing. It now quotes the exact command.
        with self.assertRaises(ValueError) as caught:
            self.config.require_model_id("mail.activity")
        self.assertIn(persist_model_id_command("mail.activity"), str(caught.exception))


class TestSetModelIdToml(unittest.TestCase):
    """The writer edits one key of one section and leaves the rest alone (#890)."""

    def _write_and_reload(self, content, model="project.task", ir_model_id=71):
        with TemporaryDirectory() as tmp:
            path = _write(tmp, "config.toml", content)
            with patch.dict("os.environ", {}, clear=True):
                config = LocalConfig.load(config_path=path)
                written = config.set_model_id(model, ir_model_id)
                reloaded = LocalConfig.load(config_path=path)
            return config, reloaded, Path(path).read_text(encoding="utf-8"), written

    def test_creates_the_section_when_absent(self):
        _, reloaded, text, written = self._write_and_reload(
            '[connection]\nurl = "https://x"\n'
        )
        self.assertEqual(reloaded.model_ids, {"project.task": 71})
        self.assertIn("[model_ids]", text)
        self.assertEqual(written.name, "config.toml")

    def test_preserves_every_other_section_key_and_comment(self):
        _, reloaded, text, _ = self._write_and_reload(
            "# hand-written header\n"
            '[connection]\nurl = "https://x"\ndb = "prod"\n\n'
            "[behavior]\nprofiling = true\n"
        )
        self.assertIn("# hand-written header", text)
        self.assertEqual(reloaded.connection["url"], "https://x")
        self.assertEqual(reloaded.connection["db"], "prod")
        self.assertEqual(reloaded.behavior["profiling"], True)
        self.assertEqual(reloaded.model_ids["project.task"], 71)

    def test_appends_into_an_existing_section_without_disturbing_siblings(self):
        _, reloaded, _, _ = self._write_and_reload('[model_ids]\n"res.partner" = 12\n')
        self.assertEqual(reloaded.model_ids, {"res.partner": 12, "project.task": 71})

    def test_updates_a_quoted_key_in_place(self):
        _, reloaded, text, _ = self._write_and_reload(
            '[model_ids]\n"project.task" = 5\n"res.partner" = 12\n'
        )
        self.assertEqual(reloaded.model_ids, {"project.task": 71, "res.partner": 12})
        # Updated, not appended: exactly one entry for the model.
        self.assertEqual(text.count("project.task"), 1)

    def test_updates_an_unquoted_dotted_key_in_place(self):
        _, reloaded, text, _ = self._write_and_reload("[model_ids]\nproject.task = 5\n")
        self.assertEqual(reloaded.model_ids, {"project.task": 71})
        self.assertEqual(text.count("project.task"), 1)

    def test_updates_a_subtable_key_in_place(self):
        _, reloaded, text, _ = self._write_and_reload("[model_ids.project]\ntask = 5\n")
        self.assertEqual(reloaded.model_ids, {"project.task": 71})
        self.assertIn("[model_ids.project]", text)
        self.assertIn("task = 71", text)

    def test_an_entry_in_a_later_section_is_not_mistaken_for_one(self):
        _, reloaded, text, _ = self._write_and_reload(
            "[model_ids]\n"
            '"res.partner" = 12\n\n'
            "[behavior]\n"
            "resync_window_days = 5\n"
        )
        self.assertEqual(reloaded.behavior["resync_window_days"], 5)
        self.assertEqual(reloaded.model_ids["project.task"], 71)
        # Appended against the section's last entry, not against the blank line
        # that separates it from [behavior] — the new key must not drift down
        # into the gap and leave the two sections touching.
        self.assertEqual(
            text,
            "[model_ids]\n"
            '"res.partner" = 12\n'
            '"project.task" = 71\n'
            "\n"
            "[behavior]\n"
            "resync_window_days = 5\n",
        )

    def test_the_in_memory_map_is_updated_too(self):
        config, _, _, _ = self._write_and_reload('[connection]\nurl = "https://x"\n')
        self.assertEqual(config.model_id("project.task"), 71)

    def test_rejects_a_non_positive_id_without_touching_the_file(self):
        with TemporaryDirectory() as tmp:
            path = _write(tmp, "config.toml", '[connection]\nurl = "https://x"\n')
            with patch.dict("os.environ", {}, clear=True):
                config = LocalConfig.load(config_path=path)
                for bad in (0, -3, "abc", True, 1.5):
                    with self.subTest(value=bad):
                        with self.assertRaises(ValueError):
                            config.set_model_id("project.task", bad)
            self.assertNotIn("model_ids", Path(path).read_text(encoding="utf-8"))

    def test_the_env_override_is_never_written(self):
        # Precedence is unchanged: the file entry the writer adds simply wins,
        # and ODOO_MODEL_IDS is left exactly as the operator set it.
        with TemporaryDirectory() as tmp:
            path = _write(tmp, "config.toml", '[connection]\nurl = "https://x"\n')
            env = {MODEL_IDS_ENV_VAR: "res.users:7"}
            with patch.dict("os.environ", env, clear=True):
                LocalConfig.load(config_path=path).set_model_id("project.task", 71)
                self.assertEqual(os.environ[MODEL_IDS_ENV_VAR], "res.users:7")
                reloaded = LocalConfig.load(config_path=path)
            self.assertEqual(reloaded.model_ids, {"project.task": 71, "res.users": 7})


class TestSetModelIdIni(unittest.TestCase):
    """The same writer, against the other shape the loader accepts."""

    def _write_and_reload(self, content):
        with TemporaryDirectory() as tmp:
            path = _write(tmp, "config.ini", content)
            with patch.dict("os.environ", {}, clear=True):
                LocalConfig.load(config_path=path).set_model_id("project.task", 71)
                reloaded = LocalConfig.load(config_path=path)
            return reloaded, Path(path).read_text(encoding="utf-8")

    def test_creates_the_section_when_absent(self):
        reloaded, text = self._write_and_reload("[connection]\nurl = https://x\n")
        self.assertEqual(reloaded.model_ids, {"project.task": 71})
        # Unquoted: a quoted key is a TOML spelling configparser would keep
        # literally, producing a model named '"project.task"'.
        self.assertIn("project.task = 71", text)
        self.assertEqual(reloaded.connection["url"], "https://x")

    def test_updates_an_existing_entry_in_place(self):
        reloaded, text = self._write_and_reload(
            "[model_ids]\nproject.task = 5\nres.partner = 12\n"
        )
        self.assertEqual(reloaded.model_ids, {"project.task": 71, "res.partner": 12})
        self.assertEqual(text.count("project.task"), 1)

    def test_a_colon_delimited_entry_is_updated_not_duplicated(self):
        reloaded, text = self._write_and_reload("[model_ids]\nproject.task: 5\n")
        self.assertEqual(reloaded.model_ids, {"project.task": 71})
        self.assertEqual(text.count("project.task"), 1)

    def test_an_upper_cased_entry_is_updated_not_duplicated(self):
        # configparser lowercases option names on load, so Project.Task IS the
        # project.task entry; appending a second one would shadow it silently.
        reloaded, text = self._write_and_reload("[model_ids]\nProject.Task = 5\n")
        self.assertEqual(reloaded.model_ids, {"project.task": 71})
        self.assertEqual(text.lower().count("project.task"), 1)


class TestSetModelIdDestination(unittest.TestCase):
    """Where the writer writes when there is nothing to edit yet."""

    def test_a_config_directory_override_gets_a_config_toml(self):
        with TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {}, clear=True):
                config = LocalConfig.load(config_path=tmp)
                written = config.set_model_id("project.task", 71)
                reloaded = LocalConfig.load(config_path=tmp)
        self.assertEqual(written, Path(tmp) / "config.toml")
        self.assertEqual(reloaded.model_ids, {"project.task": 71})

    def test_an_existing_ini_in_that_directory_is_edited_not_shadowed(self):
        # The loader probes config.toml before config.ini; creating a new TOML
        # beside an existing INI would silently orphan the operator's file.
        with TemporaryDirectory() as tmp:
            _write(tmp, "config.ini", "[connection]\nurl = https://x\n")
            with patch.dict("os.environ", {}, clear=True):
                written = LocalConfig.load(config_path=tmp).set_model_id(
                    "project.task", 71
                )
        self.assertEqual(written, Path(tmp) / "config.ini")
        self.assertFalse((Path(tmp) / "config.toml").exists())

    def test_an_unreachable_destination_raises_the_reportable_error(self):
        with TemporaryDirectory() as tmp:
            # A plain file where the config directory would be: no chmod, so
            # this stays a real failure even for a root-owned test runner.
            blocker = _write(tmp, "blocked", "")
            with patch.dict("os.environ", {}, clear=True):
                config = LocalConfig.load(config_path=f"{blocker}/config.toml")
                with self.assertRaises(ModelIdsNotWritableError) as caught:
                    config.set_model_id("project.task", 71)
        message = str(caught.exception)
        self.assertIn("project.task", message)
        # Degrades to the pre-existing workflow rather than dead-ending.
        self.assertIn("by hand", message)

    def test_a_write_the_loader_would_reject_is_rolled_back(self):
        # A root-level inline table is a spelling the loader accepts and the
        # line-based writer does not model; appending a [model_ids] section
        # beside it is a TOML duplicate. The original file must survive.
        original = 'model_ids = { "project.task" = 5 }\n'
        with TemporaryDirectory() as tmp:
            path = _write(tmp, "config.toml", original)
            with patch.dict("os.environ", {}, clear=True):
                config = LocalConfig.load(config_path=path)
                self.assertEqual(config.model_id("project.task"), 5)
                with self.assertRaises(ModelIdsNotWritableError):
                    config.set_model_id("project.task", 71)
            self.assertEqual(Path(path).read_text(encoding="utf-8"), original)

    @unittest.skipIf(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        "root ignores the write bit, so an unwritable file cannot be simulated",
    )
    def test_an_unwritable_file_raises_the_reportable_error(self):
        with TemporaryDirectory() as tmp:
            path = _write(tmp, "config.toml", '[connection]\nurl = "https://x"\n')
            with patch.dict("os.environ", {}, clear=True):
                config = LocalConfig.load(config_path=path)
                os.chmod(path, 0o444)
                try:
                    with self.assertRaises(ModelIdsNotWritableError) as caught:
                        config.set_model_id("project.task", 71)
                finally:
                    os.chmod(path, 0o644)
        message = str(caught.exception)
        self.assertIn("project.task", message)
        # Degrades to the pre-existing workflow rather than dead-ending.
        self.assertIn("by hand", message)


if __name__ == "__main__":
    unittest.main()
