import configparser
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_CONFIG_ENV_VAR = "odoo_sdk_CONFIG"
DEFAULT_SECTION = "odoo"
DEFAULT_CONFIG_LOCATIONS = (
    ".odoo_sdk.ini",
    "~/.config/odoo_sdk/config.ini",
)
DEFAULT_DOTENV_LOCATIONS = ("~/.config/odoo_sdk/.env",)
CONNECTION_ENVIRONMENT_VARIABLES = {
    "url": "ODOO_URL",
    "db": "ODOO_DB",
    "username": "ODOO_USERNAME",
    "password": "ODOO_PASSWORD",
}


@dataclass(frozen=True)
class OdooConnectionSettings:
    url: str
    db: str
    username: str
    password: str

    @classmethod
    def from_sources(
        cls,
        *,
        url: Optional[str] = None,
        db: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        config_path: Optional[str] = None,
    ) -> "OdooConnectionSettings":
        dotenv_values = _load_dotenv_values()
        file_values = _load_file_values(config_path, dotenv_values)
        environment_values = _load_environment_values(dotenv_values)
        # Prefer explicit `None` checks so callers can pass empty strings
        # deliberately (the INI loader still treats empty values as missing).
        explicit_values = {
            "url": url,
            "db": db,
            "username": username,
            "password": password,
        }
        values: dict[str, Optional[str]] = {}
        for key, explicit_value in explicit_values.items():
            if explicit_value is not None:
                values[key] = explicit_value
            elif key in environment_values:
                values[key] = environment_values[key]
            else:
                values[key] = file_values.get(key)

        # Treat both `None` and empty string as missing configuration values.
        missing = [key for key, value in values.items() if value in (None, "")]
        if missing:
            missing_names = ", ".join(sorted(missing))
            raise ValueError(
                "Missing Odoo connection settings: "
                f"{missing_names}. Configure them in .env, the INI file, or "
                "override them with constructor arguments."
            )

        return cls(
            url=str(values["url"]),
            db=str(values["db"]),
            username=str(values["username"]),
            password=str(values["password"]),
        )


def _load_environment_values(dotenv_values: dict[str, str]) -> dict[str, str]:
    resolved_values: dict[str, str] = {}
    for key, environment_name in CONNECTION_ENVIRONMENT_VARIABLES.items():
        if environment_name in os.environ:
            resolved_values[key] = os.environ[environment_name]
            continue
        if environment_name in dotenv_values:
            resolved_values[key] = dotenv_values[environment_name]
    return resolved_values


def _load_file_values(
    config_path: Optional[str], dotenv_values: dict[str, str]
) -> dict[str, str]:
    parser = configparser.ConfigParser()
    selected_path = _resolve_config_path(
        config_path
        or os.environ.get(DEFAULT_CONFIG_ENV_VAR)
        or dotenv_values.get(DEFAULT_CONFIG_ENV_VAR)
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
    }


def _load_dotenv_values() -> dict[str, str]:
    dotenv_path = _resolve_default_dotenv_path()
    if dotenv_path is None:
        return {}

    values: dict[str, str] = {}
    for raw_line in Path(dotenv_path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue

        name, raw_value = line.split("=", 1)
        normalized_name = name.strip()
        if not normalized_name:
            continue
        values[normalized_name] = _strip_optional_quotes(raw_value.strip())

    return values


def _resolve_default_dotenv_path() -> Optional[str]:
    cwd_candidate = Path.cwd() / ".env"
    if cwd_candidate.is_file():
        return str(cwd_candidate)

    invoking_script_candidate = _resolve_relative_to_invoking_script(Path(".env"))
    if invoking_script_candidate is not None:
        return invoking_script_candidate

    for candidate in DEFAULT_DOTENV_LOCATIONS:
        expanded_candidate = Path(candidate).expanduser()
        if expanded_candidate.is_file():
            return str(expanded_candidate)
    return None


def _strip_optional_quotes(raw_value: str) -> str:
    if len(raw_value) >= 2 and raw_value[0] == raw_value[-1]:
        if raw_value[0] in {"'", '"'}:
            return raw_value[1:-1]
    return raw_value


def _resolve_config_path(config_path: Optional[str]) -> Optional[str]:
    if config_path:
        expanded_path = Path(config_path).expanduser()
        if expanded_path.is_absolute():
            return str(expanded_path) if expanded_path.is_file() else None

        caller_relative_path = _resolve_relative_to_invoking_script(expanded_path)
        if caller_relative_path is not None:
            return caller_relative_path

        return str(expanded_path.resolve()) if expanded_path.is_file() else None

    for candidate in DEFAULT_CONFIG_LOCATIONS:
        expanded_candidate = Path(candidate).expanduser()
        if expanded_candidate.is_file():
            return str(expanded_candidate)
    return None


def _resolve_relative_to_invoking_script(config_path: Path) -> Optional[str]:
    main_module = sys.modules.get("__main__")
    main_file = getattr(main_module, "__file__", None)
    if not main_file:
        return None

    candidate = Path(main_file).resolve().parent / config_path
    return str(candidate) if candidate.is_file() else None
