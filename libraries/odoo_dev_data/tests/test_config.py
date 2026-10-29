"""Tests for odoo_dev_data.config: the TOML reader, precedence and the writer."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from odoo_dev_data.config import (
    MODEL_IDS_ENV_VAR,
    ConnectionValues,
    LocalConfig,
    ModelIdsNotWritableError,
    connection_values,
    persist_model_id_command,
)

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


def _load(tmp_path: Path, content: str | None = None, **env: str) -> LocalConfig:
    path = tmp_path / "config.toml"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    return LocalConfig.load(env={"ODOO_DEV_HOME": str(tmp_path), **env})


def test_defaults_without_file_and_env(tmp_path):
    config = _load(tmp_path)
    assert config.connection["transport"] == "xmlrpc"
    assert config.connection["url"] is None
    assert config.model_ids == {}
    assert config.session_gap_mins == 60
    assert config.session_gap_secs == 3600
    assert config.min_session_hours == 0.25
    assert config.round_session_hours == 0.05
    assert config.calendar_tick_mins == 5
    assert config.ingest_subjects is True
    assert config.profiling is False
    assert config.resync_window_days == 30
    assert config.resync_authors == []
    assert str(config.day_bucket_tz) == "America/Chicago"
    assert config.path == tmp_path / "config.toml"


def test_default_path_follows_home_env(tmp_path):
    assert LocalConfig.load(env={"ODOO_DEV_HOME": str(tmp_path)}).path == (
        tmp_path / "config.toml"
    )


def test_env_wins_over_default(tmp_path):
    config = _load(tmp_path, ODOO_URL="https://env", ODOO_SESSION_GAP_MINS="15")
    assert config.connection["url"] == "https://env"
    assert config.session_gap_mins == 15


def test_file_wins_over_env(tmp_path):
    config = _load(
        tmp_path,
        '[connection]\nurl = "https://file"\n[behavior]\nsession_gap_mins = 20\n',
        ODOO_URL="https://env",
        ODOO_SESSION_GAP_MINS="15",
        ODOO_DB="envdb",
    )
    assert config.connection["url"] == "https://file"
    assert config.connection["db"] == "envdb"
    assert config.session_gap_mins == 20


def test_github_account_unset_is_none(tmp_path):
    assert _load(tmp_path).github_account is None


def test_github_account_from_env_alone(tmp_path):
    assert _load(tmp_path, ODOO_GITHUB_ACCOUNT="work").github_account == "work"


def test_github_account_file_wins_over_env(tmp_path):
    config = _load(
        tmp_path, '[github]\naccount = "filed"\n', ODOO_GITHUB_ACCOUNT="work"
    )
    assert config.github_account == "filed"


def test_empty_file_value_falls_through_to_env(tmp_path):
    config = _load(tmp_path, '[connection]\nurl = ""\n', ODOO_URL="https://env")
    assert config.connection["url"] == "https://env"


def test_invalid_toml_raises(tmp_path):
    with pytest.raises(tomllib.TOMLDecodeError):
        _load(tmp_path, "[connection\n")


@pytest.mark.parametrize(
    "raw, expected",
    [
        (True, True),
        ("yes", True),
        ("ON", True),
        ("1", True),
        ("no", False),
        (False, False),
    ],
)
def test_profiling_flag(tmp_path, raw, expected):
    assert LocalConfig(behavior={"profiling": raw}).profiling is expected


@pytest.mark.parametrize(
    "raw, expected", [("7", 7), (9, 9), ("0", 60), (-3, 60), ("x", 60), (None, 60)]
)
def test_positive_int_coercion(raw, expected):
    assert LocalConfig(behavior={"session_gap_mins": raw}).session_gap_mins == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("0.5", 0.5),
        (2, 2.0),
        (0, 0.0),
        ("-1", 0.25),
        ("abc", 0.25),
        (True, 0.25),
        ("inf", 0.25),
    ],
)
def test_float_coercion(raw, expected):
    assert (
        LocalConfig(behavior={"min_session_hours": raw}).min_session_hours == expected
    )


def test_ingest_subjects_off(tmp_path):
    assert _load(tmp_path, ODOO_INGEST_SUBJECTS="false").ingest_subjects is False


def test_day_bucket_tz_valid_and_invalid():
    assert str(LocalConfig(behavior={"day_bucket_tz": "UTC"}).day_bucket_tz) == "UTC"
    for bad in ("Not/AZone", "", None):
        config = LocalConfig(behavior={"day_bucket_tz": bad})
        assert str(config.day_bucket_tz) == "America/Chicago"


def test_resync_authors_from_string_and_list(tmp_path):
    from_string = _load(tmp_path, ODOO_RESYNC_AUTHORS="alice, bob@x.io  alice")
    assert from_string.resync_authors == ["alice", "bob@x.io"]
    from_list = _load(tmp_path, '[behavior]\nresync_authors = ["a", "b", "a", ""]\n')
    assert from_list.resync_authors == ["a", "b"]


def test_get_returns_behavior_or_default(tmp_path):
    config = _load(tmp_path)
    assert config.get("session_gap_mins") == 60
    assert config.get("missing", "d") == "d"


# -- model ids -------------------------------------------------------------


def test_model_ids_from_file_spellings(tmp_path):
    config = _load(
        tmp_path,
        '[model_ids]\n"project.task" = 1\nres.partner = 2\n[model_ids.account]\nmove = 3\n',
    )
    assert config.model_ids == {"project.task": 1, "res.partner": 2, "account.move": 3}


def test_model_ids_from_env_and_file_union(tmp_path):
    config = _load(
        tmp_path,
        '[model_ids]\n"project.task" = 55\n',
        **{MODEL_IDS_ENV_VAR: "project.task:999,res.users:7 res.partner:3"},
    )
    assert config.model_ids == {"project.task": 55, "res.users": 7, "res.partner": 3}


def test_model_ids_env_only(tmp_path):
    assert _load(tmp_path, **{MODEL_IDS_ENV_VAR: "a.b:4"}).model_ids == {"a.b": 4}


def test_model_ids_returned_map_is_a_copy():
    config = LocalConfig(model_ids={"a.b": "5"})
    config.model_ids["c.d"] = 1
    assert config.model_ids == {"a.b": 5}


@pytest.mark.parametrize("token", ["project.task", ":5", "project.task=5"])
def test_malformed_env_token_raises(tmp_path, token):
    with pytest.raises(ValueError, match=MODEL_IDS_ENV_VAR):
        _load(tmp_path, **{MODEL_IDS_ENV_VAR: token})


@pytest.mark.parametrize("value", ["0", "-4", "abc", "true", "1.5"])
def test_bad_model_id_raises(tmp_path, value):
    with pytest.raises(ValueError, match="positive integer"):
        _load(
            tmp_path,
            f'[model_ids]\n"a.b" = {value if value[0] in "0-1" else repr(value)}\n',
        )


def test_bool_model_id_raises(tmp_path):
    with pytest.raises(ValueError, match="positive integer"):
        _load(tmp_path, '[model_ids]\n"a.b" = true\n')


def test_require_model_id(tmp_path):
    config = _load(tmp_path, '[model_ids]\n"a.b" = 6\n')
    assert config.require_model_id("a.b") == 6
    assert config.model_id("x.y") is None
    with pytest.raises(ValueError) as info:
        config.require_model_id("x.y")
    message = str(info.value)
    assert "'x.y'" in message
    assert '"x.y" = <id>' in message
    assert MODEL_IDS_ENV_VAR in message
    assert persist_model_id_command("x.y") in message
    assert "odoo-dev cmd get_models" in message


# -- set_model_id -----------------------------------------------------------


def test_set_model_id_creates_file_in_existing_home(tmp_path):
    config = _load(tmp_path)
    assert config.set_model_id("project.task", 12) == tmp_path / "config.toml"
    text = (tmp_path / "config.toml").read_text()
    assert text == '[model_ids]\n"project.task" = 12\n'
    assert config.model_id("project.task") == 12


def test_set_model_id_appends_block_and_keeps_prefix(tmp_path):
    original = '# my note\n[connection]\nurl = "https://x"   # keep\n'
    config = _load(tmp_path, original)
    config.set_model_id("res.partner", 3)
    text = (tmp_path / "config.toml").read_text()
    assert text.startswith(original)
    assert tomllib.loads(text)["model_ids"] == {"res.partner": 3}


def test_set_model_id_appends_after_file_without_trailing_newline(tmp_path):
    config = _load(tmp_path, "[connection]\nurl = 'x'")
    config.set_model_id("a.b", 1)
    parsed = tomllib.loads((tmp_path / "config.toml").read_text())
    assert parsed == {"connection": {"url": "x"}, "model_ids": {"a.b": 1}}


def test_set_model_id_replaces_block_keeping_other_entries(tmp_path):
    original = (
        "[connection]\nurl = 'x'\n\n"
        '[model_ids]  # ids\n# gone\nres.partner = 2\n"a.b" = 1\n\n'
        "[behavior]\nprofiling = true\n"
    )
    config = _load(tmp_path, original)
    config.set_model_id("a.b", 10)
    config.set_model_id("c.d", 4)
    text = (tmp_path / "config.toml").read_text()
    assert text == (
        "[connection]\nurl = 'x'\n\n"
        '[model_ids]\n"a.b" = 10\n"c.d" = 4\n"res.partner" = 2\n\n'
        "[behavior]\nprofiling = true\n"
    )
    assert config.model_ids == {"a.b": 10, "c.d": 4, "res.partner": 2}


def test_set_model_id_round_trips_through_load(tmp_path):
    _load(tmp_path).set_model_id("project.task", 8)
    assert _load(tmp_path).model_ids == {"project.task": 8}


def test_set_model_id_rejects_bad_id(tmp_path):
    config = _load(tmp_path)
    with pytest.raises(ValueError):
        config.set_model_id("a.b", 0)
    assert not (tmp_path / "config.toml").exists()


def test_set_model_id_missing_parent_raises(tmp_path):
    config = LocalConfig(path=tmp_path / "missing" / "config.toml")
    with pytest.raises(ModelIdsNotWritableError, match="could not write"):
        config.set_model_id("a.b", 1)
    assert not (tmp_path / "missing").exists()


def test_set_model_id_invalid_existing_file_raises(tmp_path):
    (tmp_path / "config.toml").write_text("[connection\n")
    config = LocalConfig(path=tmp_path / "config.toml")
    with pytest.raises(ModelIdsNotWritableError):
        config.set_model_id("a.b", 1)
    assert (tmp_path / "config.toml").read_text() == "[connection\n"


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores permissions"
)
def test_set_model_id_read_only_directory_raises(tmp_path):
    config = _load(tmp_path, "[connection]\nurl = 'x'\n")
    tmp_path.chmod(0o500)
    try:
        with pytest.raises(ModelIdsNotWritableError):
            config.set_model_id("a.b", 1)
    finally:
        tmp_path.chmod(0o700)
    assert config.model_ids == {}


# -- connection_values -------------------------------------------------------


def test_connection_values_from_config(tmp_path):
    config = _load(
        tmp_path,
        '[connection]\nurl = "https://x"\ndb = "d"\nusername = "u"\npassword = "p"\n',
        ODOO_TIMEOUT="12",
    )
    values = connection_values(config)
    assert values == ConnectionValues(
        url="https://x",
        db="d",
        username="u",
        password="p",
        api_key=None,
        transport="xmlrpc",
        timeout="12",
    )
    assert "password" not in repr(values)


def test_connection_values_explicit_overrides_non_none(tmp_path):
    config = _load(tmp_path, '[connection]\nurl = "https://x"\ndb = "d"\n')
    values = connection_values(
        config, url="https://y", db=None, api_key="k", timeout=5.0
    )
    assert (values.url, values.db, values.api_key, values.timeout) == (
        "https://y",
        "d",
        "k",
        5.0,
    )
    assert "api_key" not in repr(values)


def test_connection_values_unknown_key_raises(tmp_path):
    with pytest.raises(TypeError, match="bogus"):
        connection_values(_load(tmp_path), bogus=1)
