"""Local configuration for the Odoo SDK.

This module hosts two related concerns of the local state layer:

* :class:`OdooConnectionSettings` — the resolved, validated connection value
  object consumed by :class:`~odoo_sdk.client.client.OdooClient`. It resolves
  from explicit args, environment variables, and INI files.
* :class:`LocalConfig` — a first-class, read-only settings object promoted to a
  peer dependency of commands (alongside ``OdooClient`` and
  ``LocalStateClient``). It resolves each setting with the precedence

      File  >  Environment Variable  >  Sensible Default

  so that consuming programs (Claude Desktop, other MCP hosts) can change SDK
  behavior by editing a local config file without touching the host launch
  command. A ``[behavior]`` section is reserved for future behavioral flags
  (profiling, log level, ...) without further structural changes.
"""

import configparser
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, Optional

DEFAULT_CONFIG_ENV_VAR = "odoo_sdk_CONFIG"
DEFAULT_SECTION = "odoo"
CONNECTION_ENV_VARS = {
    "url": "ODOO_URL",
    "db": "ODOO_DB",
    "username": "ODOO_USERNAME",
    "password": "ODOO_PASSWORD",
    "api_key": "ODOO_API_KEY",
    "transport": "ODOO_TRANSPORT",
}
DEFAULT_CONFIG_LOCATIONS = (
    ".odoo_sdk.ini",
    "~/.config/odoo_sdk/config.ini",
)

# LocalConfig discovery: default config file path and the env var that overrides
# it. The file is the highest-precedence source (File > Env > Default).
LOCAL_CONFIG_ENV_VAR = "ODOO_SDK_CONFIG"
DEFAULT_LOCAL_CONFIG_PATH = "~/.config/odoo-sdk/config.toml"


@dataclass(frozen=True)
class OdooConnectionSettings:
    """Hold resolved connection settings for constructing the default client.

    This value object is necessary because the client can source configuration from
    explicit arguments, environment variables, and INI files, but the executor only
    needs one validated set of concrete connection strings.

    :param url: Base URL of the Odoo server.
    :type url: str
    :param db: Database name to authenticate against.
    :type db: str
    :param username: Username used for authentication.
    :type username: str
    :param password: Password or API key used for authentication.
    :type password: str
    """

    url: str
    db: str
    username: Optional[str] = None
    password: Optional[str] = None
    transport: Literal["xmlrpc", "json2"] = "xmlrpc"
    api_key: Optional[str] = field(default=None, repr=False)

    @classmethod
    def from_sources(
        cls,
        *,
        url: Optional[str] = None,
        db: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        api_key: Optional[str] = None,
        transport: Optional[str] = None,
        config_path: Optional[str] = None,
    ) -> "OdooConnectionSettings":
        """Resolve connection settings from explicit, environment, and file sources.

        This factory is necessary because the client supports multiple configuration
        channels and must apply one predictable precedence order before validation.

        :param url: Explicit URL override, defaults to None.
        :type url: Optional[str]
        :param db: Explicit database override, defaults to None.
        :type db: Optional[str]
        :param username: Explicit username override, defaults to None.
        :type username: Optional[str]
        :param password: Explicit password override, defaults to None.
        :type password: Optional[str]
        :param config_path: Optional INI file path override, defaults to None.
        :type config_path: Optional[str]
        :raises ValueError: Raised when any required setting remains unresolved.
        :return: Fully resolved connection settings.
        :rtype: OdooConnectionSettings
        """
        file_values = _load_file_values(config_path)
        environment_values = _load_environment_values()
        # Prefer explicit `None` checks so callers can pass empty strings
        # deliberately (the INI loader still treats empty values as missing).
        explicit_values = {
            "url": url,
            "db": db,
            "username": username,
            "password": password,
            "api_key": api_key,
            "transport": transport,
        }
        values: dict[str, Optional[str]] = {
            key: _resolve_setting_value(
                key, explicit_value, environment_values, file_values
            )
            for key, explicit_value in explicit_values.items()
        }

        resolved_transport = _resolve_transport(values)
        _validate_required_settings(values, resolved_transport)

        return cls(
            url=str(values["url"]),
            db=str(values["db"]),
            username=values.get("username") or None,
            password=values.get("password") or None,
            transport=resolved_transport,
            api_key=values.get("api_key") or None,
        )


def _resolve_transport(values: dict[str, Optional[str]]) -> Literal["xmlrpc", "json2"]:
    """Return the effective transport type from resolved values.

    This helper is necessary so transport resolution is isolated from from_sources
    and keeps its cyclomatic complexity within acceptable bounds.

    :param values: Resolved setting values keyed by setting name.
    :type values: dict[str, Optional[str]]
    :return: Effective transport type.
    :rtype: Literal["xmlrpc", "json2"]
    """
    return "json2" if values.get("transport") == "json2" else "xmlrpc"


def _validate_required_settings(
    values: dict[str, Optional[str]],
    transport: Literal["xmlrpc", "json2"],
) -> None:
    """Raise ValueError when required settings are absent for the given transport.

    This helper is necessary so transport-aware validation is isolated from
    from_sources and keeps its cyclomatic complexity within acceptable bounds.

    :param values: Resolved setting values keyed by setting name.
    :type values: dict[str, Optional[str]]
    :param transport: Effective transport type.
    :type transport: Literal["xmlrpc", "json2"]
    :raises ValueError: When required keys are missing.
    """
    if transport == "json2":
        required = ("url", "db", "api_key")
        missing = [key for key in required if not values.get(key)]
    else:
        required = ("url", "db", "username", "password")
        missing = [key for key in required if values.get(key) in (None, "")]

    if missing:
        missing_names = ", ".join(sorted(missing))
        raise ValueError(
            "Missing Odoo connection settings: "
            f"{missing_names}. Configure them with environment variables, "
            "the INI file, or override them with constructor arguments."
        )


def _load_file_values(config_path: Optional[str]) -> dict[str, str]:
    """Load connection settings from the selected INI file, if one exists.

    This helper is necessary so file-based configuration stays isolated from source
    precedence logic and can return an empty mapping when no valid file applies.

    :param config_path: Explicit or environment-provided config path, defaults to
        None.
    :type config_path: Optional[str]
    :return: Connection values loaded from the INI file.
    :rtype: dict[str, str]
    """
    parser = configparser.ConfigParser()
    selected_path = _resolve_config_path(
        config_path or os.environ.get(DEFAULT_CONFIG_ENV_VAR)
    )
    if selected_path is None:
        return {}

    parser.read(selected_path)
    if not parser.has_section(DEFAULT_SECTION):
        return {}

    return {
        "url": parser.get(DEFAULT_SECTION, "url", fallback=""),
        "db": parser.get(DEFAULT_SECTION, "db", fallback=""),
        "username": parser.get(DEFAULT_SECTION, "username", fallback=""),
        "password": parser.get(DEFAULT_SECTION, "password", fallback=""),
        "api_key": parser.get(DEFAULT_SECTION, "api_key", fallback=""),
        "transport": parser.get(DEFAULT_SECTION, "transport", fallback=""),
    }


def _load_environment_values() -> dict[str, Optional[str]]:
    """Load connection settings from the configured environment variables.

    This helper is necessary because environment values participate in the supported
    configuration precedence order for client construction.

    :return: Environment-derived connection values keyed by setting name.
    :rtype: dict[str, Optional[str]]
    """
    return {
        key: os.environ.get(environment_variable)
        for key, environment_variable in CONNECTION_ENV_VARS.items()
    }


def _resolve_config_path(config_path: Optional[str]) -> Optional[str]:
    """Resolve the effective INI file path from explicit and default locations.

    This helper is necessary because callers may provide relative paths, absolute
    paths, or rely on default search locations, and only existing files should be
    returned to the loader.

    :param config_path: Explicit config path to resolve, defaults to None.
    :type config_path: Optional[str]
    :return: Absolute path to an existing config file, or None when none applies.
    :rtype: Optional[str]
    """
    if config_path:
        expanded_path = Path(config_path).expanduser()
        if expanded_path.is_absolute():
            return str(expanded_path) if expanded_path.is_file() else None

        return _resolve_relative_to_invoking_script(expanded_path) or (
            str(expanded_path.resolve()) if expanded_path.is_file() else None
        )

    for candidate in DEFAULT_CONFIG_LOCATIONS:
        expanded = Path(candidate).expanduser()
        if expanded.is_file():
            return str(expanded)
    return None


def _resolve_setting_value(
    key: str,
    explicit: Optional[str],
    env_vals: dict[str, Optional[str]],
    file_vals: dict[str, str],
) -> Optional[str]:
    """Resolve one connection setting from the three-tier precedence chain.

    This helper is necessary because the precedence logic (explicit > env > file)
    should be isolated, testable, and readable rather than embedded in a nested
    ternary comprehension.

    :param key: Setting name used to look up values in env and file dicts.
    :type key: str
    :param explicit: Explicit value override, or None when not provided.
    :type explicit: Optional[str]
    :param env_vals: Environment-derived values keyed by setting name.
    :type env_vals: dict[str, Optional[str]]
    :param file_vals: File-derived values keyed by setting name.
    :type file_vals: dict[str, str]
    :return: Resolved setting value, or None when all sources are absent.
    :rtype: Optional[str]
    """
    if explicit is not None:
        return explicit
    if env_vals.get(key) is not None:
        return env_vals.get(key)
    return file_vals.get(key)


def _resolve_relative_to_invoking_script(config_path: Path) -> Optional[str]:
    """Resolve a relative config path against the invoking script directory.

    This helper is necessary because consumers often run scripts from different
    working directories, but still expect a relative config path beside the script to
    resolve predictably.

    :param config_path: Relative path provided by the caller.
    :type config_path: Path
    :return: Absolute path to an existing script-relative config file, or None.
    :rtype: Optional[str]
    """
    main_module = sys.modules.get("__main__")
    main_file = getattr(main_module, "__file__", None)
    if not main_file:
        return None

    candidate = Path(main_file).resolve().parent / config_path
    return str(candidate) if candidate.is_file() else None


# ── LocalConfig ───────────────────────────────────────────────────────────────

# Sensible defaults applied at the lowest precedence (File > Env > Default).
_CONNECTION_DEFAULTS: dict[str, Optional[str]] = {
    "url": None,
    "db": None,
    "username": None,
    "password": None,
    "api_key": None,
    "transport": "xmlrpc",
}

# String tokens interpreted as an enabled boolean flag. INI and environment
# values arrive as strings, so behavior flags stored as strings are coerced
# against this shared set (TOML booleans and defaults arrive as real bools).
_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})

# The fixed sessionization inactivity gap, in minutes. Seeded from the pure
# core's historical default (``DEFAULT_WINDOW_GAP_SECS`` = 3600s = 60 min). This
# gap is a stable session-identity constant, not a per-run tuning knob: it is
# what the incremental sessionizer uses to decide session boundaries.
_DEFAULT_SESSION_GAP_MINS = 60

# Environment variables that override behavior settings when no file value is set.
_BEHAVIOR_ENV_VARS: dict[str, str] = {
    "profiling": "ODOO_PROFILING",
    "session_gap_mins": "ODOO_SESSION_GAP_MINS",
}

# Sensible defaults for the reserved [behavior] section.
_BEHAVIOR_DEFAULTS: dict[str, Any] = {
    "profiling": False,
    "session_gap_mins": _DEFAULT_SESSION_GAP_MINS,
}


class LocalConfig:
    """Resolved, read-only SDK settings promoted to the local state layer.

    ``LocalConfig`` is injected into commands as a peer dependency alongside
    ``OdooClient`` and ``LocalStateClient``. Each setting is resolved with the
    precedence **File > Environment Variable > Sensible Default**, so the local
    config file always wins when present.

    :param connection: Resolved connection settings keyed by setting name.
    :type connection: Mapping[str, Optional[str]]
    :param behavior: Resolved behavior settings (reserved for future flags).
    :type behavior: Mapping[str, Any]
    """

    def __init__(
        self,
        connection: Optional[Mapping[str, Optional[str]]] = None,
        behavior: Optional[Mapping[str, Any]] = None,
    ):
        self._connection: dict[str, Optional[str]] = {
            **_CONNECTION_DEFAULTS,
            **(dict(connection) if connection else {}),
        }
        self._behavior: dict[str, Any] = {
            **_BEHAVIOR_DEFAULTS,
            **(dict(behavior) if behavior else {}),
        }

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> "LocalConfig":
        """Resolve settings from file, environment, and defaults.

        :param config_path: Explicit config file path override; when omitted the
            ``ODOO_SDK_CONFIG`` env var and default path are consulted.
        :type config_path: Optional[str]
        :return: A resolved, read-only ``LocalConfig``.
        :rtype: LocalConfig
        """
        file_data = _load_local_config_file(config_path)
        connection = _resolve_section(
            file_data.get("connection", {}),
            CONNECTION_ENV_VARS,
            _CONNECTION_DEFAULTS,
        )
        behavior = _resolve_section(
            file_data.get("behavior", {}),
            _BEHAVIOR_ENV_VARS,
            _BEHAVIOR_DEFAULTS,
        )
        return cls(connection=connection, behavior=behavior)

    @property
    def connection(self) -> Mapping[str, Optional[str]]:
        """Return the resolved connection settings as a read-only mapping."""
        return dict(self._connection)

    @property
    def behavior(self) -> Mapping[str, Any]:
        """Return the resolved behavior settings as a read-only mapping."""
        return dict(self._behavior)

    def get(self, key: str, default: Any = None) -> Any:
        """Return one resolved behavior setting, or ``default`` when absent."""
        return self._behavior.get(key, default)

    @property
    def profiling(self) -> bool:
        """Return whether per-call MCP profiling is enabled.

        Resolved from the ``[behavior] profiling`` file setting, the
        ``ODOO_PROFILING`` environment variable, or the default (disabled), with
        the standard File > Environment Variable > Default precedence. String
        sources (``"1"``, ``"true"``, ``"yes"``, ``"on"``) are treated as truthy.

        :return: True when profiling should be enabled, False otherwise.
        :rtype: bool
        """
        value = self._behavior.get("profiling", False)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in _TRUTHY_VALUES

    @property
    def session_gap_mins(self) -> int:
        """Return the fixed sessionization inactivity gap in minutes.

        Resolved from the ``[behavior] session_gap_mins`` file setting, the
        ``ODOO_SESSION_GAP_MINS`` environment variable, or the default
        (``60``), with the standard File > Environment Variable > Default
        precedence. String sources are coerced to ``int``; an invalid value
        falls back to the default rather than raising.

        This gap is a stable session-identity constant: sessions are boundaries
        of runs separated by more than this gap, and the value must not change
        per query or the identity of already-detected sessions would shift.

        :return: The inactivity gap in whole minutes.
        :rtype: int
        """
        value = self._behavior.get("session_gap_mins", _DEFAULT_SESSION_GAP_MINS)
        try:
            gap = int(value)
        except (TypeError, ValueError):
            return _DEFAULT_SESSION_GAP_MINS
        return gap if gap > 0 else _DEFAULT_SESSION_GAP_MINS

    @property
    def session_gap_secs(self) -> int:
        """Return the fixed sessionization inactivity gap in whole seconds."""
        return self.session_gap_mins * 60

    def connection_settings(self) -> OdooConnectionSettings:
        """Build validated :class:`OdooConnectionSettings` from resolved values.

        :raises ValueError: When required connection settings are unresolved.
        :return: Validated connection settings for client construction.
        :rtype: OdooConnectionSettings
        """
        conn = self._connection
        return OdooConnectionSettings.from_sources(
            url=conn.get("url"),
            db=conn.get("db"),
            username=conn.get("username"),
            password=conn.get("password"),
            api_key=conn.get("api_key"),
            transport=conn.get("transport"),
        )


def _resolve_local_config_path(config_path: Optional[str]) -> Optional[Path]:
    """Return the config file to read, honoring explicit path, env var, default.

    Only an existing file is returned; a missing file yields ``None`` so callers
    fall back to environment variables and defaults.

    :param config_path: Explicit path override, defaults to None.
    :type config_path: Optional[str]
    :return: Path to an existing config file, or None.
    :rtype: Optional[Path]
    """
    candidate = (
        config_path
        or os.environ.get(LOCAL_CONFIG_ENV_VAR)
        or DEFAULT_LOCAL_CONFIG_PATH
    )
    path = Path(candidate).expanduser()
    return path if path.is_file() else None


def _load_local_config_file(config_path: Optional[str]) -> dict[str, dict[str, Any]]:
    """Load ``[connection]`` and ``[behavior]`` sections from the config file.

    Supports TOML (``.toml``) and INI files. Returns an empty mapping when no
    file applies.

    :param config_path: Explicit path override, defaults to None.
    :type config_path: Optional[str]
    :return: Parsed sections keyed by section name.
    :rtype: dict[str, dict[str, Any]]
    """
    path = _resolve_local_config_path(config_path)
    if path is None:
        return {}
    if path.suffix == ".toml":
        return _load_toml_sections(path)
    return _load_ini_sections(path)


def _load_toml_sections(path: Path) -> dict[str, dict[str, Any]]:
    """Parse the ``connection`` and ``behavior`` tables from a TOML file."""
    import tomllib

    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return {
        "connection": dict(data.get("connection", {})),
        "behavior": dict(data.get("behavior", {})),
    }


def _load_ini_sections(path: Path) -> dict[str, dict[str, Any]]:
    """Parse the ``connection`` and ``behavior`` sections from an INI file."""
    parser = configparser.ConfigParser()
    parser.read(path)
    sections: dict[str, dict[str, Any]] = {}
    for name in ("connection", "behavior"):
        if parser.has_section(name):
            sections[name] = dict(parser.items(name))
    return sections


def _resolve_section(
    file_values: Mapping[str, Any],
    env_vars: Mapping[str, str],
    defaults: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge one section with File > Environment Variable > Default precedence.

    :param file_values: Values read from the config file for this section.
    :type file_values: Mapping[str, Any]
    :param env_vars: Mapping of setting name to environment variable name.
    :type env_vars: Mapping[str, str]
    :param defaults: Sensible defaults for this section.
    :type defaults: Mapping[str, Any]
    :return: Resolved values keyed by setting name.
    :rtype: dict[str, Any]
    """
    keys = set(defaults) | set(env_vars) | set(file_values)
    resolved: dict[str, Any] = {}
    for key in keys:
        if key in file_values and file_values[key] not in (None, ""):
            resolved[key] = file_values[key]
            continue
        env_name = env_vars.get(key)
        env_value = os.environ.get(env_name) if env_name else None
        if env_value not in (None, ""):
            resolved[key] = env_value
            continue
        resolved[key] = defaults.get(key)
    return resolved
