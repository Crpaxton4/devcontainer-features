import configparser
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

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
            key: _resolve_setting_value(key, explicit_value, environment_values, file_values)
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

        return (
            _resolve_relative_to_invoking_script(expanded_path)
            or (str(expanded_path.resolve()) if expanded_path.is_file() else None)
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
