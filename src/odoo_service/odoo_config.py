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
        file_values = _load_file_values(config_path)
        # Prefer explicit `None` checks so callers can pass empty strings
        # deliberately (the INI loader still treats empty values as missing).
        values = {
            "url": url if url is not None else file_values.get("url"),
            "db": db if db is not None else file_values.get("db"),
            "username": username if username is not None else file_values.get("username"),
            "password": password if password is not None else file_values.get("password"),
        }
        # Treat both `None` and empty string as missing configuration values.
        missing = [key for key, value in values.items() if value in (None, "")]
        if missing:
            missing_names = ", ".join(sorted(missing))
            raise ValueError(
                "Missing Odoo connection settings: "
                f"{missing_names}. Configure them in the INI file or override them "
                "with constructor arguments."
            )

        return cls(
            url=str(values["url"]),
            db=str(values["db"]),
            username=str(values["username"]),
            password=str(values["password"]),
        )


def _load_file_values(config_path: Optional[str]) -> dict[str, str]:
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
    }


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
