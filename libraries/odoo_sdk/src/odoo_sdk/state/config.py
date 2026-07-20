"""Local configuration for the Odoo SDK.

This module hosts two related concerns of the local state layer:

* :class:`LocalConfig` — the single, first-class settings resolver. It discovers
  one config file (see :data:`LOCAL_CONFIG_ENV_VAR` and the default locations
  below) and resolves each setting with the precedence

      File  >  Environment Variable  >  Sensible Default

  so that consuming programs (Claude Desktop, other MCP hosts) can change SDK
  behavior by editing a local config file without touching the host launch
  command. A ``[behavior]`` section is reserved for future behavioral flags
  (profiling, log level, ...) without further structural changes.
* :class:`OdooConnectionSettings` — the resolved, validated connection value
  object consumed by :class:`~odoo_sdk.client.client.OdooClient`. Its
  :meth:`~OdooConnectionSettings.from_sources` factory is a thin validator fed by
  :class:`LocalConfig`: it resolves file, environment, and default values through
  the single resolver and then overlays any explicit constructor arguments.

Config discovery consults, in order, the first location that yields an existing
file:

1. ``$ODOO_SDK_CONFIG`` — a config **file** or a **directory** that is probed for
   ``config.toml`` then ``config.ini`` (so the devcontainer feature can point the
   variable at its mounted config directory regardless of which file exists).
2. ``./.odoo_sdk.toml`` / ``./.odoo_sdk.ini`` in the current working directory.
3. ``~/.config/odoo_sdk/config.toml`` / ``~/.config/odoo_sdk/config.ini``.

INI files accept ``[odoo]`` as an alias for ``[connection]`` so an already
persisted ``~/.config/odoo_sdk/config.ini`` keeps working unchanged.
"""

import configparser
import importlib
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# The single environment variable that overrides config discovery. It may name a
# config FILE or a DIRECTORY (the directory is probed for config.toml /
# config.ini), so the devcontainer feature can point it at the mounted config
# directory regardless of which file the user created.
LOCAL_CONFIG_ENV_VAR = "ODOO_SDK_CONFIG"

# INI section holding connection settings, plus the legacy ``[odoo]`` alias
# accepted so an already-persisted ``~/.config/odoo_sdk/config.ini`` keeps
# working, and the reserved ``[behavior]`` section.
_CONNECTION_SECTION = "connection"
_CONNECTION_SECTION_ALIAS = "odoo"
_BEHAVIOR_SECTION = "behavior"

# Default config discovery locations (see the module docstring for the full
# precedence order). ``$ODOO_SDK_CONFIG`` overrides all of these.
_CWD_CONFIG_BASENAMES = (".odoo_sdk.toml", ".odoo_sdk.ini")
DEFAULT_CONFIG_DIR = "~/.config/odoo_sdk"
_CONFIG_DIR_FILENAMES = ("config.toml", "config.ini")

# TOML parser modules probed, in order, when a ``.toml`` config is read. The
# stdlib ``tomllib`` only exists from Python 3.11; on 3.10 (the supported floor,
# shipped by ``odoo:17``) the API-compatible ``tomli`` backport stands in for it
# and is declared as a ``python_version < "3.11"`` dependency.
_TOML_MODULE_NAMES = ("tomllib", "tomli")

#: Default per-request transport timeout, in seconds. The single source of this
#: number: it is the fallback for garbage or absent ``ODOO_TIMEOUT`` values and the
#: dataclass field default, and both transports re-export it (as
#: ``DEFAULT_REQUEST_TIMEOUT_SECONDS``) rather than redefining the literal.
DEFAULT_TIMEOUT_SECONDS: float = 30.0

CONNECTION_ENV_VARS = {
    "url": "ODOO_URL",
    "db": "ODOO_DB",
    "username": "ODOO_USERNAME",
    "password": "ODOO_PASSWORD",
    "api_key": "ODOO_API_KEY",
    "transport": "ODOO_TRANSPORT",
    "timeout": "ODOO_TIMEOUT",
}


@dataclass(frozen=True)
class OdooConnectionSettings:
    """Resolved, validated connection settings consumed by the executor.

    One concrete set of connection strings distilled from explicit arguments,
    environment variables, and INI files.
    """

    url: str
    db: str
    username: Optional[str] = None
    password: Optional[str] = field(default=None, repr=False)
    transport: Literal["xmlrpc", "json2"] = "xmlrpc"
    timeout: float = DEFAULT_TIMEOUT_SECONDS
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
        timeout: Optional[float] = None,
        config_path: Optional[str] = None,
    ) -> "OdooConnectionSettings":
        """Resolve connection settings via :class:`LocalConfig`, then validate.

        A thin validator over the single resolver: it resolves file, environment, and
        default values through :meth:`LocalConfig.load` (precedence **File >
        Environment Variable > Default**) and overlays any explicit constructor
        arguments (which win over every resolved source).

        :raises ValueError: When any required setting remains unresolved.
        """
        resolved: dict[str, Any] = dict(LocalConfig.load(config_path).connection)
        # Prefer explicit `None` checks so callers can pass empty strings
        # deliberately; validation still treats empty values as missing.
        explicit_values = {
            "url": url,
            "db": db,
            "username": username,
            "password": password,
            "api_key": api_key,
            "transport": transport,
            "timeout": timeout,
        }
        for key, explicit_value in explicit_values.items():
            if explicit_value is not None:
                resolved[key] = explicit_value
        return _build_connection_settings(resolved)


def _build_connection_settings(values: Mapping[str, Any]) -> OdooConnectionSettings:
    """Validate a resolved connection mapping and build the value object.

    The single place transport selection, required-setting validation, and timeout
    coercion happen, so :meth:`OdooConnectionSettings.from_sources` and
    :meth:`LocalConfig.connection_settings` behave identically.

    :raises ValueError: When any required setting remains unresolved.
    """
    resolved_transport: Literal["xmlrpc", "json2"] = (
        "json2" if values.get("transport") == "json2" else "xmlrpc"
    )
    _validate_required_settings(values, resolved_transport)
    return OdooConnectionSettings(
        url=str(values["url"]),
        db=str(values["db"]),
        username=values.get("username") or None,
        password=values.get("password") or None,
        transport=resolved_transport,
        timeout=_coerce_timeout(values.get("timeout")),
        api_key=values.get("api_key") or None,
    )


def _validate_required_settings(
    values: Mapping[str, Any],
    transport: Literal["xmlrpc", "json2"],
) -> None:
    """Raise ValueError when required settings are absent for the given transport."""
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
            "the config file, or override them with constructor arguments."
        )


def _coerce_non_negative_float(value: Any, default: float) -> float:
    """Coerce a raw value to a non-negative finite float, else ``default``.

    Values arrive from environment variables and INI files as strings, may be
    absent, or may be garbage; anything that is not a finite number ``>= 0``
    degrades to ``default`` rather than raising, so a mistyped config never crashes
    an upload. Booleans are rejected explicitly because ``float(True)`` is ``1.0``,
    which would silently turn ``= true`` into a one-hour floor.
    """
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isfinite(number) and number >= 0:
        return number
    return default


def _coerce_timeout(value: Any) -> float:
    """Coerce a raw timeout to a strictly-positive float, else :data:`DEFAULT_TIMEOUT_SECONDS`."""
    coerced = _coerce_non_negative_float(value, DEFAULT_TIMEOUT_SECONDS)
    return coerced if coerced > 0 else DEFAULT_TIMEOUT_SECONDS


def _coerce_positive_int(value: Any, default: int) -> int:
    """Coerce a raw value to a positive ``int``, else ``default`` (mirrors the float form)."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _coerce_flag(value: Any) -> bool:
    """Coerce a bool or truthy string token (``1``/``true``/``yes``/``on``) to a flag."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUTHY_VALUES


# ── LocalConfig ───────────────────────────────────────────────────────────────

# Sensible defaults applied at the lowest precedence (File > Env > Default).
_CONNECTION_DEFAULTS: dict[str, Optional[str]] = {
    "url": None,
    "db": None,
    "username": None,
    "password": None,
    "api_key": None,
    "transport": "xmlrpc",
    # The concrete numeric default lives in ``DEFAULT_TIMEOUT_SECONDS``;
    # ``_build_connection_settings`` coerces this absent value into it.
    "timeout": None,
}

# String tokens interpreted as an enabled boolean flag. INI and environment
# values arrive as strings, so behavior flags stored as strings are coerced
# against this shared set (TOML booleans and defaults arrive as real bools).
_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})

# The fixed sessionization inactivity gap, in minutes. Seeded from the pure
# core's historical default (``DEFAULT_WINDOW_GAP_SECS`` = 3600s = 60 min). This
# gap is a stable session-identity constant, not a per-run tuning knob: it is
# what the SQL-derived read path uses to decide session boundaries.
_DEFAULT_SESSION_GAP_MINS = 60

# Billing policy defaults for the derived-session upload path (issue #355).
# A derived session bills its wall-clock span, but a single-event session spans
# zero seconds and a very short session rounds toward nothing, so raw span
# silently under-bills. ``min_session_hours`` is the floor every billable
# session is raised to; ``round_session_hours`` is the multiple the span is
# rounded to (nearest, half-up). A rounding step of ``0`` disables rounding.
_DEFAULT_MIN_SESSION_HOURS = 0.25
_DEFAULT_ROUND_SESSION_HOURS = 0.05

# Google Calendar / Gmail ingestion defaults (issue #370). ``calendar_tick_mins``
# is the constant interval at which a meeting is expanded into synthetic point
# events so the existing gap derivation reconstructs it as one session; it MUST
# stay strictly below the session gap and the sweep floor (validated at resync,
# acceptance #11). ``ingest_subjects`` controls whether an ingested meeting/email
# subject is stored. ``google_sync_window_days`` is the backward/forward reconcile
# window (calendar mutates retroactively, so the window looks both ways).
_DEFAULT_CALENDAR_TICK_MINS = 5
_DEFAULT_INGEST_SUBJECTS = True
_DEFAULT_GOOGLE_SYNC_WINDOW_DAYS = 30

# Resync-capture defaults (issue #378). ``day_bucket_tz`` is the IANA timezone the
# scoring/rendering day-bucketing uses; it was a hardcoded EDT offset that
# mis-bucketed the US-Central user's midnight-crossing evening sessions, so it is
# now config-driven and defaults to US Central. ``resync_window_days`` bounds the
# git ``--since`` / GitHub / chatter resync queries so re-runs stay cheap.
# ``resync_authors`` is the list of author identities (GitHub logins and/or git
# emails) the pullers capture for; empty means "the active login / git email".
_DEFAULT_DAY_BUCKET_TZ = "America/Chicago"
_DEFAULT_RESYNC_WINDOW_DAYS = 30

# Environment variables that override behavior settings when no file value is set.
_BEHAVIOR_ENV_VARS: dict[str, str] = {
    "profiling": "ODOO_PROFILING",
    "session_gap_mins": "ODOO_SESSION_GAP_MINS",
    "min_session_hours": "ODOO_MIN_SESSION_HOURS",
    "round_session_hours": "ODOO_ROUND_SESSION_HOURS",
    "calendar_tick_mins": "ODOO_CALENDAR_TICK_MINS",
    "ingest_subjects": "ODOO_INGEST_SUBJECTS",
    "google_sync_window_days": "ODOO_GOOGLE_SYNC_WINDOW_DAYS",
    "google_token_path": "ODOO_GOOGLE_TOKEN_PATH",
    "day_bucket_tz": "ODOO_DAY_BUCKET_TZ",
    "resync_window_days": "ODOO_RESYNC_WINDOW_DAYS",
    "resync_authors": "ODOO_RESYNC_AUTHORS",
}

# Sensible defaults for the reserved [behavior] section.
_BEHAVIOR_DEFAULTS: dict[str, Any] = {
    "profiling": False,
    "session_gap_mins": _DEFAULT_SESSION_GAP_MINS,
    "min_session_hours": _DEFAULT_MIN_SESSION_HOURS,
    "round_session_hours": _DEFAULT_ROUND_SESSION_HOURS,
    "calendar_tick_mins": _DEFAULT_CALENDAR_TICK_MINS,
    "ingest_subjects": _DEFAULT_INGEST_SUBJECTS,
    "google_sync_window_days": _DEFAULT_GOOGLE_SYNC_WINDOW_DAYS,
    "google_token_path": None,
    "day_bucket_tz": _DEFAULT_DAY_BUCKET_TZ,
    "resync_window_days": _DEFAULT_RESYNC_WINDOW_DAYS,
    "resync_authors": None,
}


class LocalConfig:
    """Resolved, read-only SDK settings promoted to the local state layer.

    The single settings resolver, injected into commands as a peer dependency
    alongside ``OdooClient`` and ``LocalStateClient``. Each setting is resolved with
    the precedence **File > Environment Variable > Sensible Default**, so the local
    config file always wins when present.
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

        When ``config_path`` is omitted the ``ODOO_SDK_CONFIG`` env var and the
        default discovery locations are consulted.
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
        """Return whether per-call MCP profiling is enabled (``[behavior] profiling``)."""
        return _coerce_flag(self._behavior.get("profiling", False))

    @property
    def session_gap_mins(self) -> int:
        """Return the fixed sessionization inactivity gap in minutes (default ``60``).

        A stable session-identity constant: sessions are runs separated by more than
        this gap, and the value must not change per query or the identity of
        already-detected sessions would shift. An invalid value falls back to the
        default rather than raising.
        """
        return _coerce_positive_int(
            self._behavior.get("session_gap_mins"), _DEFAULT_SESSION_GAP_MINS
        )

    @property
    def session_gap_secs(self) -> int:
        """Return the fixed sessionization inactivity gap in whole seconds."""
        return self.session_gap_mins * 60

    @property
    def min_session_hours(self) -> float:
        """Return the per-session billing floor in hours (default ``0.25``, #355).

        Every billable derived session is raised to at least this many hours, so a
        single-event session (zero wall-clock span) bills the minimum rather than
        nothing. ``0`` is honoured (no floor); an invalid value falls back to the
        default.
        """
        return _coerce_non_negative_float(
            self._behavior.get("min_session_hours"), _DEFAULT_MIN_SESSION_HOURS
        )

    @property
    def round_session_hours(self) -> float:
        """Return the per-session rounding step in hours (default ``0.05``, #355).

        A billable session's wall-clock span is rounded to the nearest multiple of
        this step (half-up). ``0`` is honoured and disables rounding (the raw span is
        billed, subject to the minimum); an invalid value falls back to the default.
        """
        return _coerce_non_negative_float(
            self._behavior.get("round_session_hours"), _DEFAULT_ROUND_SESSION_HOURS
        )

    @property
    def calendar_tick_mins(self) -> int:
        """Return the meeting-expansion tick interval in minutes (default ``5``, #370).

        A meeting is expanded into synthetic point events this many minutes apart
        (with a terminal tick on the exact end) so the gap derivation reconstructs it
        as a single session. The invariant that this stay strictly below both the
        session gap and the sweep floor is enforced at resync, not here. An invalid
        value falls back to the default.
        """
        return _coerce_positive_int(
            self._behavior.get("calendar_tick_mins"), _DEFAULT_CALENDAR_TICK_MINS
        )

    @property
    def ingest_subjects(self) -> bool:
        """Return whether ingested meeting/email subjects are stored (default on, #370).

        Any non-truthy string disables subject capture so a client's correspondence
        titles can be kept out of the central DB.
        """
        return _coerce_flag(
            self._behavior.get("ingest_subjects", _DEFAULT_INGEST_SUBJECTS)
        )

    @property
    def google_sync_window_days(self) -> int:
        """Return the Google reconcile window radius in days (default ``30``, #370).

        Calendar mutates retroactively and the sent window is backward-looking, so a
        resync reconciles events within this many days each side of now. An invalid
        value falls back to the default.
        """
        return _coerce_positive_int(
            self._behavior.get("google_sync_window_days"),
            _DEFAULT_GOOGLE_SYNC_WINDOW_DAYS,
        )

    @property
    def google_token_path(self) -> Optional[str]:
        """Return an explicit Google token file path override, or None (#370).

        When unset the puller derives the path from the existing ``ODOO_SDK_CONFIG``
        mount, so the token lives beside the other host-provisioned SDK config.
        """
        value = self._behavior.get("google_token_path")
        return str(value) if value else None

    @property
    def day_bucket_tz(self) -> ZoneInfo:
        """Return the day-bucketing timezone (default ``America/Chicago``, #378).

        Scoring and rendering bucket a session's wall-clock span onto a calendar day
        in this zone; a wrong zone mis-buckets evening sessions that cross local
        midnight. An unknown or malformed IANA key falls back to the default.
        """
        value = self._behavior.get("day_bucket_tz") or _DEFAULT_DAY_BUCKET_TZ
        try:
            return ZoneInfo(str(value))
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo(_DEFAULT_DAY_BUCKET_TZ)

    @property
    def resync_window_days(self) -> int:
        """Return the resync-capture window radius in days (default ``30``, #378).

        Bounds the git ``--since``, GitHub, and Odoo-chatter resync queries so a
        re-run scans only recent history. An invalid value falls back to the default.
        """
        return _coerce_positive_int(
            self._behavior.get("resync_window_days"), _DEFAULT_RESYNC_WINDOW_DAYS
        )

    @property
    def resync_authors(self) -> list[str]:
        """Return the configured resync author identities in first-seen order (#378 item 4).

        The author identities the GitHub/git pullers capture work for — GitHub logins
        and/or git commit emails (an entry with ``@`` is treated as an email matched
        against ``git log``; one without as a GitHub login). Accepts a TOML list or a
        comma/whitespace-separated string. When empty, each puller falls back to its
        active identity, so single-account users need no config.
        """
        value = self._behavior.get("resync_authors")
        if value in (None, ""):
            return []
        raw = value if isinstance(value, (list, tuple)) else re.split(r"[,\s]+", str(value))
        seen: list[str] = []
        for item in raw:
            identity = str(item).strip()
            if identity and identity not in seen:
                seen.append(identity)
        return seen

    def connection_settings(self) -> OdooConnectionSettings:
        """Build validated :class:`OdooConnectionSettings` from the resolved values.

        The connection mapping has already been resolved (File > Env > Default) by
        :meth:`load`, so this validates and coerces it directly.

        :raises ValueError: When required connection settings are unresolved.
        """
        return _build_connection_settings(self._connection)


def _resolve_local_config_path(config_path: Optional[str]) -> Optional[Path]:
    """Return the config file to read, honoring the override, env var, defaults.

    The explicit override and the ``ODOO_SDK_CONFIG`` env var may name either a
    config **file** or a **directory**; a directory is probed for ``config.toml``
    then ``config.ini`` so the devcontainer feature can point ``ODOO_SDK_CONFIG``
    at its mounted config directory regardless of which file the user created.
    When no override applies, the current working directory and then the default
    ``~/.config/odoo_sdk`` directory are searched. Only an existing file is
    returned; otherwise ``None`` so callers fall back to environment variables and
    defaults.
    """
    candidate = config_path or os.environ.get(LOCAL_CONFIG_ENV_VAR)
    if candidate:
        return _resolve_config_candidate(Path(candidate).expanduser())
    for basename in _CWD_CONFIG_BASENAMES:
        cwd_path = Path(basename)
        if cwd_path.is_file():
            return cwd_path
    return _probe_config_dir(Path(DEFAULT_CONFIG_DIR).expanduser())


def _resolve_config_candidate(path: Path) -> Optional[Path]:
    """Resolve an explicit or env-provided candidate to an existing config file.

    A directory candidate is probed for the known config filenames; a file
    candidate is returned when it exists; anything else yields ``None`` (and, per
    the single-override contract, does not fall through to the default locations).
    """
    if path.is_dir():
        return _probe_config_dir(path)
    return path if path.is_file() else None


def _probe_config_dir(directory: Path) -> Optional[Path]:
    """Return the first existing ``config.toml`` / ``config.ini`` in ``directory``."""
    for filename in _CONFIG_DIR_FILENAMES:
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    return None


def _load_local_config_file(config_path: Optional[str]) -> dict[str, dict[str, Any]]:
    """Load ``[connection]`` and ``[behavior]`` sections from the config file.

    Supports TOML (``.toml``) and INI files. Returns an empty mapping when no
    file applies.
    """
    path = _resolve_local_config_path(config_path)
    if path is None:
        return {}
    if path.suffix == ".toml":
        return _load_toml_sections(path)
    return _load_ini_sections(path)


def _import_toml_module() -> ModuleType:
    """Return a module exposing ``load(binary_file)``, tolerating Python 3.10.

    ``tomllib`` is stdlib only from Python 3.11, but the supported floor is 3.10
    (``odoo:17`` ships 3.10), where the ``tomli`` backport provides the identical
    ``load`` API. Probed in order and imported lazily, so ``import odoo_sdk``
    never depends on a TOML parser and INI-only hosts are unaffected.

    :raises RuntimeError: When neither module is importable.
    """
    for module_name in _TOML_MODULE_NAMES:
        try:
            return importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
    raise RuntimeError(
        "Reading a TOML config file needs the stdlib 'tomllib' (Python 3.11+) or "
        "the 'tomli' backport on Python 3.10; neither is importable. Install "
        "'tomli' or use an INI config file instead."
    )


def _load_toml_sections(path: Path) -> dict[str, dict[str, Any]]:
    """Parse the ``connection`` and ``behavior`` tables from a TOML file."""
    toml = _import_toml_module()
    with path.open("rb") as handle:
        data = toml.load(handle)
    return {
        "connection": dict(data.get("connection", {})),
        "behavior": dict(data.get("behavior", {})),
    }


def _load_ini_sections(path: Path) -> dict[str, dict[str, Any]]:
    """Parse the ``connection`` and ``behavior`` sections from an INI file.

    ``[odoo]`` is accepted as an alias for ``[connection]`` (used only when no
    explicit ``[connection]`` section is present) so an already-persisted
    ``~/.config/odoo_sdk/config.ini`` keeps working unchanged.
    """
    parser = configparser.ConfigParser()
    parser.read(path)
    sections: dict[str, dict[str, Any]] = {}
    if parser.has_section(_CONNECTION_SECTION):
        sections["connection"] = dict(parser.items(_CONNECTION_SECTION))
    elif parser.has_section(_CONNECTION_SECTION_ALIAS):
        sections["connection"] = dict(parser.items(_CONNECTION_SECTION_ALIAS))
    if parser.has_section(_BEHAVIOR_SECTION):
        sections["behavior"] = dict(parser.items(_BEHAVIOR_SECTION))
    return sections


def _resolve_section(
    file_values: Mapping[str, Any],
    env_vars: Mapping[str, str],
    defaults: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge one section with File > Environment Variable > Default precedence."""
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
